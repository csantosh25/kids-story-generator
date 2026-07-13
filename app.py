from fastapi import FastAPI, Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import uuid

from services.theme_service import ThemeService

app = FastAPI(title="Kids Story Generator")

# Background job store
jobs = {}

# Static folders
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/output", StaticFiles(directory="output"), name="output")

templates = Jinja2Templates(directory="templates")


def run_story(job_id: str, theme: str):
    """Runs story generation in the background."""

    from pipelines.story_pipeline import StoryPipeline

    # Callback used by StoryPipeline to update progress
    def progress_callback(progress, step):
        jobs[job_id]["progress"] = progress
        jobs[job_id]["step"] = step

    try:

        pipeline = StoryPipeline(
            progress_callback=progress_callback
        )

        result = pipeline.run(theme)

        jobs[job_id]["status"] = "completed"
        jobs[job_id]["progress"] = 100
        jobs[job_id]["step"] = "✅ Completed"
        jobs[job_id]["result"] = result

    except Exception as e:

        jobs[job_id]["status"] = "failed"
        jobs[job_id]["step"] = "❌ Failed"
        jobs[job_id]["error"] = str(e)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):

    themes = ThemeService().themes

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "request": request,
            "themes": themes,
        },
    )


@app.post("/generate")
async def generate_story(
    background_tasks: BackgroundTasks,
    theme_mode: str = Form(...),
    theme: str = Form(""),
):

    if theme_mode == "random":
        theme = ThemeService().get_theme()

    job_id = str(uuid.uuid4())

    jobs[job_id] = {
        "status": "running",
        "progress": 0,
        "step": "🚀 Starting...",
    }

    background_tasks.add_task(
        run_story,
        job_id,
        theme,
    )

    return RedirectResponse(
        url=f"/progress/{job_id}",
        status_code=303,
    )


@app.get("/progress/{job_id}", response_class=HTMLResponse)
async def progress(
    request: Request,
    job_id: str,
):

    if job_id not in jobs:
        return HTMLResponse(
            "<h2>Job not found.</h2>",
            status_code=404,
        )

    return templates.TemplateResponse(
        request=request,
        name="progress.html",
        context={
            "request": request,
            "job_id": job_id,
        },
    )


@app.get("/status/{job_id}")
async def status(job_id: str):

    if job_id not in jobs:
        return {
            "status": "not_found",
        }

    return jobs[job_id]


@app.get("/result/{job_id}", response_class=HTMLResponse)
async def result(
    request: Request,
    job_id: str,
):

    if job_id not in jobs:
        return HTMLResponse(
            "<h2>Job not found.</h2>",
            status_code=404,
        )

    job = jobs[job_id]

    if job["status"] == "running":
        return RedirectResponse(
            url=f"/progress/{job_id}",
            status_code=303,
        )

    if job["status"] == "failed":
        return HTMLResponse(
            f"<h2>Generation Failed</h2><p>{job['error']}</p>",
            status_code=500,
        )

    return templates.TemplateResponse(
        request=request,
        name="result.html",
        context={
            "request": request,
            "result": job["result"],
        },
    )