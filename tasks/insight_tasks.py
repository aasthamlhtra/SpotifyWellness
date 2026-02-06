"""
Celery tasks for generating AI-powered insights using LangChain + OpenAI
Handles wellness insights, roasts, and other LLM-based analysis
"""
from celery import Task
from celery_config import celery_app
from database_config import get_db_session
from database_models import ListeningSnapshot, GeneratedInsight, InsightType, BackgroundJob
from redis_config import cache
from datetime import datetime
import uuid
import os
import time
from typing import Dict, Optional
from sqlalchemy.orm import Session

# LangChain imports
from langchain_openai import ChatOpenAI
from langchain.prompts import ChatPromptTemplate
from langchain.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field
from typing import List


class InsightTask(Task):
    """Base task for insight generation with error handling"""
    autoretry_for = (Exception,)
    retry_kwargs = {'max_retries': 2, 'countdown': 30}
    retry_backoff = True


# Pydantic models for structured LLM output

class WellnessNudge(BaseModel):
    """Individual wellness nudge"""
    category: str = Field(description="Category: mood, energy, variety, balance")
    message: str = Field(description="The wellness suggestion")
    priority: str = Field(description="Priority: high, medium, low")


class WellnessInsightOutput(BaseModel):
    """Structured wellness insight output"""
    overall_assessment: str = Field(description="2-3 sentence overall wellness summary")
    wellness_nudges: List[WellnessNudge] = Field(description="3-5 specific wellness suggestions")
    key_patterns: List[str] = Field(description="3-5 key listening patterns observed")
    mood_score: float = Field(description="Overall mood score 0-10", ge=0, le=10)


class RoastOutput(BaseModel):
    """Structured roast output"""
    roast_title: str = Field(description="Witty title for the roast")
    main_roast: str = Field(description="Main roast content, 2-3 paragraphs")
    specific_callouts: List[str] = Field(description="3-5 specific funny observations")
    redemption_quality: str = Field(description="One redeeming quality about their taste")


def get_llm_client(model: str = "gpt-4-turbo-preview", temperature: float = 0.7) -> ChatOpenAI:
    """Create LangChain OpenAI client"""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not found in environment")
    
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=api_key
    )


def format_snapshot_for_llm(snapshot: ListeningSnapshot) -> str:
    """Format listening snapshot data for LLM context"""
    
    context = f"""
# Listening Snapshot Analysis

## Time Period
- Analysis range: {snapshot.time_range.value.replace('_', ' ').title()}
- Snapshot date: {snapshot.snapshot_date.strftime('%Y-%m-%d')}
- Total tracks analyzed: {snapshot.total_tracks_analyzed}

## Audio Feature Statistics
"""
    
    # Add audio features
    if snapshot.audio_features:
        for key, value in snapshot.audio_features.items():
            if isinstance(value, float):
                context += f"- {key.replace('_', ' ').title()}: {value:.3f}\n"
    
    context += "\n## Genre Distribution\n"
    if snapshot.genre_distribution:
        sorted_genres = sorted(
            snapshot.genre_distribution.items(),
            key=lambda x: x[1],
            reverse=True
        )
        for genre, percentage in sorted_genres[:10]:
            context += f"- {genre}: {percentage*100:.1f}%\n"
    
    context += "\n## Mood Patterns\n"
    if snapshot.mood_patterns:
        for mood, data in snapshot.mood_patterns.items():
            percentage = data.get("percentage", 0) * 100
            count = data.get("track_count", 0)
            context += f"- {mood.title()}: {percentage:.1f}% ({count} tracks)\n"
    
    context += f"\n## Diversity Scores\n"
    context += f"- Artist diversity: {snapshot.artist_diversity_score:.3f}\n"
    context += f"- Mood diversity: {snapshot.mood_diversity_score:.3f}\n"
    
    return context


@celery_app.task(bind=True, base=InsightTask, name="tasks.insight_tasks.generate_wellness_insight")
def generate_wellness_insight(self, snapshot_id: str, tone_mode: str = "neutral") -> Dict:
    """
    Generate wellness-focused insight from listening data
    
    Args:
        snapshot_id: Snapshot UUID string
        tone_mode: Tone of the insight (supportive, neutral, encouraging)
        
    Returns:
        Task result with insight information
    """
    db = next(get_db_session())
    start_time = time.time()
    
    try:
        # Create background job record
        job = BackgroundJob(
            job_type="generate_wellness_insight",
            celery_task_id=self.request.id,
            status="running",
            params={"snapshot_id": snapshot_id, "tone_mode": tone_mode},
            started_at=datetime.now()
        )
        db.add(job)
        db.commit()
        
        # Get snapshot
        snapshot_uuid = uuid.UUID(snapshot_id)
        snapshot = db.query(ListeningSnapshot).filter(
            ListeningSnapshot.id == snapshot_uuid
        ).first()
        
        if not snapshot:
            job.status = "failed"
            job.error_message = "Snapshot not found"
            job.completed_at = datetime.now()
            db.commit()
            raise ValueError("Snapshot not found")
        
        # Format data for LLM
        snapshot_context = format_snapshot_for_llm(snapshot)
        
        # Create LLM client
        llm = get_llm_client(model="gpt-4-turbo-preview", temperature=0.7)
        
        # Setup parser
        parser = PydanticOutputParser(pydantic_object=WellnessInsightOutput)
        
        # Create prompt based on tone
        tone_instructions = {
            "supportive": "Be warm, encouraging, and focus on positive patterns. Frame suggestions gently.",
            "neutral": "Be balanced and objective. Present both positive patterns and areas for growth.",
            "encouraging": "Be uplifting and motivating. Celebrate their listening habits while suggesting gentle improvements."
        }
        
        tone_instruction = tone_instructions.get(tone_mode, tone_instructions["neutral"])
        
        # Create prompt template
        prompt = ChatPromptTemplate.from_messages([
            ("system", f"""You are a music wellness analyst who helps people understand how their listening habits 
            relate to their emotional wellbeing. {tone_instruction}
            
            Analyze the listening data and provide actionable wellness insights.
            
            {parser.get_format_instructions()}
            """),
            ("user", """Analyze this listening data and provide wellness insights:

{snapshot_data}

Focus on:
1. Emotional patterns in their music choices
2. Variety and balance in listening habits
3. Potential mood indicators from audio features
4. Suggestions for emotional wellbeing through music
""")
        ])
        
        # Generate insight
        chain = prompt | llm | parser
        
        print(f"Generating wellness insight for snapshot {snapshot_id}")
        structured_output = chain.invoke({"snapshot_data": snapshot_context})
        
        # Calculate generation time
        generation_time_ms = int((time.time() - start_time) * 1000)
        
        # Format full narrative content
        content = f"""# Wellness Insight: {tone_mode.title()} Analysis

## Overall Assessment
{structured_output.overall_assessment}

## Mood Score
Your overall mood score based on listening patterns: {structured_output.mood_score}/10

## Key Patterns Observed
"""
        for i, pattern in enumerate(structured_output.key_patterns, 1):
            content += f"{i}. {pattern}\n"
        
        content += "\n## Wellness Nudges\n"
        for nudge in structured_output.wellness_nudges:
            content += f"\n### {nudge.category.title()} ({nudge.priority.title()} Priority)\n"
            content += f"{nudge.message}\n"
        
        # Create insight record
        insight = GeneratedInsight(
            user_id=snapshot.user_id,
            snapshot_id=snapshot_uuid,
            insight_type=InsightType.WELLNESS,
            llm_model="gpt-4-turbo-preview",
            prompt_version="v1.0",
            tone_mode=tone_mode,
            content=content,
            structured_output=structured_output.dict(),
            generation_time_ms=generation_time_ms
        )
        
        db.add(insight)
        db.commit()
        db.refresh(insight)
        
        # Update job status
        job.status = "success"
        job.completed_at = datetime.now()
        job.result = {
            "insight_id": str(insight.id),
            "generation_time_ms": generation_time_ms
        }
        db.commit()
        
        # Invalidate cache
        cache.delete(f"insights:user:{snapshot.user_id}")
        
        print(f"Wellness insight generated successfully: {insight.id}")
        
        return {
            "success": True,
            "insight_id": str(insight.id),
            "snapshot_id": snapshot_id,
            "generation_time_ms": generation_time_ms,
            "mood_score": structured_output.mood_score
        }
    
    except Exception as e:
        print(f"Error generating wellness insight: {e}")
        
        # Update job status
        if 'job' in locals():
            job.status = "failed"
            job.error_message = str(e)
            job.completed_at = datetime.now()
            db.commit()
        
        raise
    
    finally:
        db.close()


@celery_app.task(bind=True, base=InsightTask, name="tasks.insight_tasks.generate_roast")
def generate_roast(self, snapshot_id: str) -> Dict:
    """
    Generate a humorous roast based on listening data
    
    Args:
        snapshot_id: Snapshot UUID string
        
    Returns:
        Task result with roast information
    """
    db = next(get_db_session())
    start_time = time.time()
    
    try:
        # Create background job record
        job = BackgroundJob(
            job_type="generate_roast",
            celery_task_id=self.request.id,
            status="running",
            params={"snapshot_id": snapshot_id},
            started_at=datetime.now()
        )
        db.add(job)
        db.commit()
        
        # Get snapshot
        snapshot_uuid = uuid.UUID(snapshot_id)
        snapshot = db.query(ListeningSnapshot).filter(
            ListeningSnapshot.id == snapshot_uuid
        ).first()
        
        if not snapshot:
            job.status = "failed"
            job.error_message = "Snapshot not found"
            job.completed_at = datetime.now()
            db.commit()
            raise ValueError("Snapshot not found")
        
        # Format data for LLM
        snapshot_context = format_snapshot_for_llm(snapshot)
        
        # Create LLM client with higher temperature for creativity
        llm = get_llm_client(model="gpt-4-turbo-preview", temperature=0.9)
        
        # Setup parser
        parser = PydanticOutputParser(pydantic_object=RoastOutput)
        
        # Create prompt template
        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a witty music critic who lovingly roasts people's music taste. 
            Be funny, creative, and playful - but never mean-spirited. Find humorous patterns and 
            contradictions in their listening habits. Think like a comedian analyzing their Spotify Wrapped.
            
            {format_instructions}
            """),
            ("user", """Roast this person's music taste based on their listening data:

{snapshot_data}

Make it funny and specific to their actual listening patterns. Include observations about:
- Genre choices and combinations
- Mood patterns and what they reveal
- Audio feature preferences (like always picking sad songs or only high-energy tracks)
- Any funny contradictions or patterns

Keep it playful and end on a positive note!
""")
        ])
        
        # Generate roast
        chain = prompt | llm | parser
        
        print(f"Generating roast for snapshot {snapshot_id}")
        structured_output = chain.invoke({
            "snapshot_data": snapshot_context,
            "format_instructions": parser.get_format_instructions()
        })
        
        # Calculate generation time
        generation_time_ms = int((time.time() - start_time) * 1000)
        
        # Format full content
        content = f"""# {structured_output.roast_title}

{structured_output.main_roast}

## Specific Observations
"""
        for i, callout in enumerate(structured_output.specific_callouts, 1):
            content += f"{i}. {callout}\n"
        
        content += f"\n## But Hey, At Least...\n{structured_output.redemption_quality}\n"
        
        # Create insight record
        insight = GeneratedInsight(
            user_id=snapshot.user_id,
            snapshot_id=snapshot_uuid,
            insight_type=InsightType.ROAST,
            llm_model="gpt-4-turbo-preview",
            prompt_version="v1.0",
            tone_mode="roast",
            content=content,
            structured_output=structured_output.dict(),
            generation_time_ms=generation_time_ms
        )
        
        db.add(insight)
        db.commit()
        db.refresh(insight)
        
        # Update job status
        job.status = "success"
        job.completed_at = datetime.now()
        job.result = {
            "insight_id": str(insight.id),
            "generation_time_ms": generation_time_ms
        }
        db.commit()
        
        # Invalidate cache
        cache.delete(f"insights:user:{snapshot.user_id}")
        
        print(f"Roast generated successfully: {insight.id}")
        
        return {
            "success": True,
            "insight_id": str(insight.id),
            "snapshot_id": snapshot_id,
            "generation_time_ms": generation_time_ms
        }
    
    except Exception as e:
        print(f"Error generating roast: {e}")
        
        # Update job status
        if 'job' in locals():
            job.status = "failed"
            job.error_message = str(e)
            job.completed_at = datetime.now()
            db.commit()
        
        raise
    
    finally:
        db.close()


@celery_app.task(bind=True, base=InsightTask, name="tasks.insight_tasks.generate_productivity_insight")
def generate_productivity_insight(self, snapshot_id: str) -> Dict:
    """
    Generate productivity-focused insight from listening data
    
    Args:
        snapshot_id: Snapshot UUID string
        
    Returns:
        Task result with insight information
    """
    db = next(get_db_session())
    start_time = time.time()
    
    try:
        # Get snapshot
        snapshot_uuid = uuid.UUID(snapshot_id)
        snapshot = db.query(ListeningSnapshot).filter(
            ListeningSnapshot.id == snapshot_uuid
        ).first()
        
        if not snapshot:
            raise ValueError("Snapshot not found")
        
        # Format data for LLM
        snapshot_context = format_snapshot_for_llm(snapshot)
        
        # Create LLM client
        llm = get_llm_client(model="gpt-4-turbo-preview", temperature=0.6)
        
        # Create prompt template
        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a productivity coach who helps people optimize their music choices 
            for focus and performance. Analyze their listening patterns for productivity indicators."""),
            ("user", """Analyze this listening data for productivity insights:

{snapshot_data}

Provide insights on:
1. Focus-conducive music patterns (instrumentals, tempo, energy)
2. Potential distractions in their listening habits
3. Recommended listening strategies for deep work
4. Balance between energizing and calming music
""")
        ])
        
        # Generate insight
        chain = prompt | llm
        
        print(f"Generating productivity insight for snapshot {snapshot_id}")
        response = chain.invoke({"snapshot_data": snapshot_context})
        
        content = response.content
        
        # Calculate generation time
        generation_time_ms = int((time.time() - start_time) * 1000)
        
        # Create insight record
        insight = GeneratedInsight(
            user_id=snapshot.user_id,
            snapshot_id=snapshot_uuid,
            insight_type=InsightType.PRODUCTIVITY,
            llm_model="gpt-4-turbo-preview",
            prompt_version="v1.0",
            tone_mode="analytical",
            content=content,
            structured_output={},
            generation_time_ms=generation_time_ms
        )
        
        db.add(insight)
        db.commit()
        db.refresh(insight)
        
        print(f"Productivity insight generated successfully: {insight.id}")
        
        return {
            "success": True,
            "insight_id": str(insight.id),
            "snapshot_id": snapshot_id,
            "generation_time_ms": generation_time_ms
        }
    
    except Exception as e:
        print(f"Error generating productivity insight: {e}")
        raise
    
    finally:
        db.close()
