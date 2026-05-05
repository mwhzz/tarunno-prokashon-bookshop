from rest_framework.routers import DefaultRouter
from django.urls import path
from .views import SaleViewSet, CustomerViewSet, report_view

router = DefaultRouter()
router.register('customers', CustomerViewSet, basename='customer')
router.register('', SaleViewSet, basename='sale')

urlpatterns = router.urls + [
    path('reports/summary/', report_view, name='report-summary'),
]
