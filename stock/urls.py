from rest_framework.routers import DefaultRouter
from .views import StockEntryViewSet, StockSummaryViewSet, StockTransferViewSet

router = DefaultRouter()
router.register('entries', StockEntryViewSet, basename='stock-entry')
router.register('summary', StockSummaryViewSet, basename='stock-summary')
router.register('transfers', StockTransferViewSet, basename='stock-transfer')

urlpatterns = router.urls
