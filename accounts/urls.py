from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import DailyCashViewSet, CashTransactionViewSet, MoneyAccountsViewSet

router = DefaultRouter()
router.register('money', MoneyAccountsViewSet, basename='money')
router.register('daily', DailyCashViewSet)
router.register('transactions', CashTransactionViewSet)

urlpatterns = [
    path('', include(router.urls)),
]
