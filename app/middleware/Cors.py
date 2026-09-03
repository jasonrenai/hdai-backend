from fastapi.middleware.cors import CORSMiddleware

def add_cors_middleware(app):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:4200",
            "http://localhost:4444",
            "https://salmon-hill-0da99d60f.5.azurestaticapps.net",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )