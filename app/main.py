from fastapi import FastAPI

app = FastAPI(title="AnythingLLM Mini")


@app.get("/")
def root():
    return {"message": "AnythingLLM Mini is running"}


@app.get("/health")
def health():
    return {"status": "ok"}
