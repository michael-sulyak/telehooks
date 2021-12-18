FROM library/python:3.10-slim


WORKDIR /app

RUN python3.10 -m pip install --upgrade setuptools && \
    python3.10 -m pip install --upgrade pip && \
    python3.10 -m pip install poetry==1.1.12
COPY ./pyproject.toml /app
COPY ./poetry.lock /app
RUN poetry config virtualenvs.create false && \
    poetry install

EXPOSE 8080
