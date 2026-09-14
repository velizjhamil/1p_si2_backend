# backend/run.py
"""Levanta uvicorn con recarga en caliente para desarrollo."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
