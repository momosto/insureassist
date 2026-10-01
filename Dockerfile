# One image, two processes: the agent service (default) and the MCP server (command override).
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY core core
COPY agent agent
COPY mcp_server mcp_server
COPY knowledge knowledge
COPY evals evals
RUN useradd --uid 10001 --no-create-home insureassist
USER 10001
EXPOSE 8100 8101
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:8100/health/live')" || exit 1
CMD ["uvicorn", "agent.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8100", "--proxy-headers"]
