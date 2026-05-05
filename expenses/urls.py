from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ExpenseCategoryViewSet, ExpenseViewSet

router = DefaultRouter()
router.register('categories', ExpenseCategoryViewSet)
router.register('records', ExpenseViewSet)

urlpatterns = [
    path('', include(router.urls)),
]
