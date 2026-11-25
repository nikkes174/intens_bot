"""Import all routers and add them to routers_list."""
from .admin import admin_router
from .echo import echo_router
from .statistics import statistics_router
from .user import user_router

routers_list = [
    admin_router,
    statistics_router,
    user_router,
    echo_router,  # echo_router must be last
]

__all__ = [
    "routers_list",
    "statistics_router",
]
