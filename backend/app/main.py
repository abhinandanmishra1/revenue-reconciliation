from fastapi import FastAPI

app = FastAPI(title="Revenue Reconciliation API", docs_url=None, redoc_url=None)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
