import logging

import uvicorn

from .config import settings


def main() -> None:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # httpx logs every request URL at INFO. The Teams webhook carries its
    # `sig` credential in the query string, so that would write the secret
    # into the deployment logs on every send.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run(
        "cns.api:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
