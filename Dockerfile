FROM library/python:3.12-slim


WORKDIR /app

RUN python3 -m pip install --upgrade setuptools && \
    python3 -m pip install --upgrade pip && \
    python3 -m pip install poetry==1.8.3
COPY ./pyproject.toml /app
COPY ./poetry.lock /app
RUN poetry config virtualenvs.create false && \
    poetry install

EXPOSE 8080
