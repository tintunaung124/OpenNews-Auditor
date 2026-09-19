from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from trafilatura import fetch_url, extract

app = FastAPI()

# frontend
app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/")
def home():
    return FileResponse("frontend/index.html")


# article request
class ArticleRequest(BaseModel):
    url: str


# audit article
@app.post("/audit")
def audit_article(request: ArticleRequest):

    # get URL
    url = request.url

    # fetch webpage
    downloaded = fetch_url(url)

    # check if webpage was downloaded
    if downloaded is None:
        return {
            "error": "Could not fetch article"
        }

    # extract article text
    article = extract(downloaded)

    # check if article was extracted
    if article is None:
        return {
            "error": "Could not extract article text"
        }

    return {
        "url": url,
        "article": article
    }