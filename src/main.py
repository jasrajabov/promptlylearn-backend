from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.routes import (
    authentication,
    chat,
    course,
    roadmap,
    tasks,
    quiz,
    payment,
    user,
    admin,
)


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(user.router)
app.include_router(authentication.router)
app.include_router(chat.router)
app.include_router(quiz.router)
app.include_router(tasks.router)
app.include_router(course.router)
app.include_router(roadmap.router)
app.include_router(payment.router)
app.include_router(admin.router)


@app.on_event("startup")
def _print_routes():
    from pprint import pprint

    routes = [(list(r.methods), r.path) for r in app.routes if getattr(r, "path", None)]
    pprint(routes)
