from rest_framework.routers import DefaultRouter

from .views import PurchaseBillViewSet, VendorViewSet

router = DefaultRouter()
router.register('vendors', VendorViewSet, basename='vendor')
router.register('bills', PurchaseBillViewSet, basename='purchase-bill')

urlpatterns = router.urls
