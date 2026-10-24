# FastAPI essentials

## Path operations

FastAPI declares endpoints with decorators such as @app.get("/items/{item_id}") and @app.post("/items"). Path parameters come from the URL, query parameters from the function signature, and type hints drive validation and the generated OpenAPI documentation at /docs.

## Request validation with Pydantic

Request bodies are declared as Pydantic models. When validation fails FastAPI returns a 422 response listing every invalid field, so route functions only ever see well typed data. Field(..., ge=1, le=50) adds numeric constraints.

## Dependency injection

Depends() shares logic such as database sessions, authentication and pagination between endpoints. A dependency can itself depend on other dependencies, and dependencies written with yield run cleanup code after the response.

## Async endpoints

Declare endpoints with async def to await non blocking I/O. Plain def endpoints run in a thread pool, so blocking calls do not freeze the event loop. Run the app with uvicorn app.main:app and use a single worker when a local embedded database is opened.

## Lifespan and middleware

The lifespan context manager opens long lived clients at startup and closes them at shutdown. CORSMiddleware controls which browser origins may call the API, and custom middleware can add request ids and timing headers.
