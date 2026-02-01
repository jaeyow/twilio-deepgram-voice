FROM dailyco/pipecat-base:latest

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy from the cache instead of linking since it's a mounted volume
ENV UV_LINK_MODE=copy

# Install the project's dependencies using the lockfile and settings
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# Copy the application code
COPY ./bot.py bot.py

# Expose port
EXPOSE 7860

# Set the entrypoint to use uv run with host binding
ENTRYPOINT ["uv", "run", "bot.py"]

# Default command arguments - bind to all interfaces for Docker
CMD ["--transport", "twilio", "--host", "0.0.0.0"]