from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

class APIException(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)

def register_exception_handlers(app):
    @app.exception_handler(APIException)
    async def api_exception_handler(request: Request, exc: APIException):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message
                }
            }
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        # Determine error code based on status code
        code_map = {
            400: "INVALID_URL",
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            409: "ALIAS_TAKEN",
            410: "EXPIRED",
            422: "UNPROCESSABLE_ENTITY",
            429: "TOO_MANY_REQUESTS",
            500: "INTERNAL_SERVER_ERROR"
        }
        code = code_map.get(exc.status_code, "ERROR")
        
        message = exc.detail
        # If detail is structured as a dict, we extract from it
        if isinstance(exc.detail, dict):
            code = exc.detail.get("code", code)
            message = exc.detail.get("message", message)
            
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": code,
                    "message": str(message)
                }
            }
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        # Check if validation error is for URL
        is_url_error = any("long_url" in err.get("loc", []) for err in errors)
        
        if is_url_error:
            code = "INVALID_URL"
            message = "The provided URL is not valid"
        else:
            code = "INVALID_INPUT"
            # Get the first error message
            if errors:
                loc_str = ".".join(str(l) for l in errors[0].get("loc", []))
                msg = errors[0].get("msg", "Invalid input")
                message = f"Validation failed for {loc_str}: {msg}"
            else:
                message = "Invalid input format"
                
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": code,
                    "message": message
                }
            }
        )
