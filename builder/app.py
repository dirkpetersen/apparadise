"""appmotel entry point: a tiny /health endpoint plus the builder's pull loop."""

import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from apparadise_builder.agent import Agent
from apparadise_builder.config import Settings, load_settings


def create_app(settings: Settings, agent: Agent | None = None) -> FastAPI:
    logging.basicConfig(level=settings.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    agent = agent or Agent(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(agent.run())
        try:
            yield
        finally:
            agent.stopping.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await agent.client.aclose()

    app = FastAPI(title="apparadise builder", docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)

    @app.get(settings.health_check_path)
    def health() -> dict[str, object]:
        return {"status": "ok", "job": agent.current}

    return app


def main() -> None:
    s = load_settings()
    uvicorn.run(create_app(s), host=s.host, port=s.port, log_level=s.log_level)


if __name__ == "__main__":
    main()
