from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging
import asyncio
from typing import List, Optional
from datetime import datetime
import os

from scrapers.madlan_scraper import MadlanScraper
from scrapers.tax_scraper import TaxAuthorityScraper
from matchers.address_matcher import AddressMatcher
from models.project import Project, ScrapeStatus
from config import Config
from ai.insights import AIInsightsGenerator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Market Survey System API",
    description="Real estate data extraction and intelligence API",
    version="1.0.0"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize components
config = Config()
madlan_scraper = MadlanScraper()
tax_scraper = TaxAuthorityScraper()
address_matcher = AddressMatcher()
ai_insights = AIInsightsGenerator()

# In-memory storage (replace with database in production)
projects_store = []
scrape_statuses = []

@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "message": "Market Survey System API",
        "version": "1.0.0",
        "endpoints": {
            "projects": "/api/projects",
            "scrape": "/api/scrape",
            "status": "/api/status"
        }
    }

@app.get("/api/projects", response_model=List[Project])
async def get_projects(
    city: Optional[str] = None,
    developer: Optional[str] = None,
    min_confidence: Optional[float] = None,
    limit: Optional[int] = 100
):
    """Get all projects with optional filtering"""
    
    filtered_projects = projects_store
    
    if city:
        filtered_projects = [p for p in filtered_projects 
                           if city.lower() in p.city.lower()]
    
    if developer:
        filtered_projects = [p for p in filtered_projects 
                           if developer.lower() in (p.developer_name or "").lower()]
    
    if min_confidence is not None:
        filtered_projects = [p for p in filtered_projects 
                           if p.data_confidence_score >= min_confidence]
    
    return filtered_projects[:limit]

@app.get("/api/projects/{project_id}", response_model=Project)
async def get_project(project_id: int):
    """Get a specific project by ID"""
    if project_id < 0 or project_id >= len(projects_store):
        raise HTTPException(status_code=404, detail="Project not found")
    
    return projects_store[project_id]

@app.post("/api/scrape")
async def trigger_scrape(
    background_tasks: BackgroundTasks,
    city: str = "tel-aviv",
    source: str = "all"
):
    """Trigger scraping for new data"""
    
    # Validate inputs
    valid_cities = ["tel-aviv", "jerusalem", "haifa", "beer-sheva", "ashdod", "ashkelon"]
    valid_sources = ["madlan", "tax", "all"]
    
    if city not in valid_cities:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid city. Must be one of: {', '.join(valid_cities)}"
        )
    
    if source not in valid_sources:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid source. Must be one of: {', '.join(valid_sources)}"
        )
    
    try:
        background_tasks.add_task(run_scraping_task, city, source)
        
        return {
            "message": "Scraping task started",
            "city": city,
            "source": source,
            "status_endpoint": "/api/status"
        }
    except Exception as e:
        logger.error(f"Error starting scraping task: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to start scraping task")

@app.get("/api/status")
async def get_scrape_status():
    """Get the latest scraping status"""
    if not scrape_statuses:
        return {"status": "no_scrapes_yet"}
    
    return scrape_statuses[-1]

@app.get("/api/export")
async def export_data(format: str = "json"):
    """Export all project data"""
    
    if format.lower() == "json":
        return JSONResponse(
            content=[project.dict() for project in projects_store],
            headers={"Content-Disposition": "attachment; filename=projects.json"}
        )
    else:
        raise HTTPException(status_code=400, detail="Unsupported format")

@app.delete("/api/projects")
async def clear_projects():
    """Clear all stored projects (for testing)"""
    global projects_store
    projects_store.clear()
    return {"message": "All projects cleared"}

@app.get("/api/ai-insights")
async def get_ai_insights():
    """Generate AI-powered market insights from current project data"""
    try:
        if not projects_store:
            return {
                "success": True,
                "insights": "No project data available for analysis. Please scrape some data first.",
                "metadata": {
                    "projects_analyzed": 0,
                    "generated_at": datetime.now().isoformat()
                }
            }
        
        insights_result = await ai_insights.generate_insights(projects_store)
        return insights_result
        
    except Exception as e:
        logger.error(f"Error generating AI insights: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to generate insights")

@app.get("/api/ai-insights/prompt")
async def get_system_prompt():
    """Get the current system prompt for AI insights"""
    return {
        "system_prompt": ai_insights.get_system_prompt()
    }

@app.post("/api/ai-insights/prompt")
async def update_system_prompt(request: Request):
    """Update the system prompt for AI insights"""
    try:
        body = await request.json()
        new_prompt = body.get("system_prompt", "").strip()
        
        if not new_prompt:
            raise HTTPException(status_code=400, detail="System prompt cannot be empty")
        
        ai_insights.update_system_prompt(new_prompt)
        
        return {
            "message": "System prompt updated successfully",
            "system_prompt": new_prompt
        }
        
    except Exception as e:
        logger.error(f"Error updating system prompt: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to update system prompt")

async def run_scraping_task(city: str, source: str):
    """Background task for scraping data"""
    
    status = ScrapeStatus(
        source=source,
        status="in_progress",
        projects_found=0
    )
    
    try:
        logger.info(f"Starting scrape for city: {city}, source: {source}")
        
        # Initialize scrapers
        projects = []
        transactions = []
        
        # Scrape based on source
        if source in ["madlan", "all"]:
            try:
                logger.info("Scraping Madlan...")
                madlan_projects = await madlan_scraper.scrape_projects(city)
                projects.extend(madlan_projects)
                logger.info(f"Found {len(madlan_projects)} projects from Madlan")
            except Exception as e:
                error_msg = f"Madlan scraping failed: {str(e)}"
                logger.error(error_msg)
                status.errors.append(error_msg)
        
        if source in ["tax", "all"]:
            try:
                logger.info("Scraping Tax Authority...")
                tax_transactions = await tax_scraper.scrape_transactions(
                    city.replace('-', ' ').title()
                )
                transactions.extend(tax_transactions)
                logger.info(f"Found {len(tax_transactions)} transactions from Tax Authority")
            except Exception as e:
                error_msg = f"Tax Authority scraping failed: {str(e)}"
                logger.error(error_msg)
                status.errors.append(error_msg)
        
        # Match transactions with projects
        if projects and transactions:
            try:
                logger.info("Matching projects with transactions...")
                matched_projects = address_matcher.match_projects_with_transactions(
                    projects, transactions
                )
                projects = matched_projects
                logger.info(f"Successfully matched {len(projects)} projects with transactions")
            except Exception as e:
                error_msg = f"Address matching failed: {str(e)}"
                logger.error(error_msg)
                status.errors.append(error_msg)
        
        # Update global store
        global projects_store
        projects_store = projects
        
        # Update status
        if status.errors:
            status.status = "completed_with_errors"
        else:
            status.status = "completed"
        status.projects_found = len(projects)
        
        logger.info(f"Scraping completed. Found {len(projects)} total projects")
        
    except Exception as e:
        logger.error(f"Scraping failed: {str(e)}")
        status.status = "failed"
        status.errors.append(f"Critical error: {str(e)}")
    
    finally:
        scrape_statuses.append(status)

@app.on_event("startup")
async def startup_event():
    """Initialize on startup"""
    logger.info("Market Survey System API starting up...")
    
    # Ensure data directory exists
    os.makedirs(config.JSON_OUTPUT_DIR, exist_ok=True)
    
    logger.info("API ready to serve requests")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("Market Survey System API shutting down...")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=config.API_HOST,
        port=config.API_PORT,
        reload=True
    )
