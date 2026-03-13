from fastapi import FastAPI


app = FastAPI()


@app.get("/healthz")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}
