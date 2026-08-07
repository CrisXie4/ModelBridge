"""API routers for the web backend."""

from . import config as config_router_module
from . import doctor as doctor_router_module
from . import models as models_router_module
from . import prompts as prompts_router_module
from . import session as session_router_module
from . import skills as skills_router_module
from . import usage as usage_router_module

# Re-export the APIRouter instances so server.py can mount them.
config_router = config_router_module.router
doctor_router = doctor_router_module.router
models_router = models_router_module.router
prompts_router = prompts_router_module.router
session_router = session_router_module.router
skills_router = skills_router_module.router
usage_router = usage_router_module.router

__all__ = [
    "config_router",
    "doctor_router",
    "models_router",
    "prompts_router",
    "session_router",
    "skills_router",
    "usage_router",
]
