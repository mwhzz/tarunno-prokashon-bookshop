from rest_framework.routers import DefaultRouter
from .views import BookViewSet, GroupViewSet

router = DefaultRouter()
router.register('groups', GroupViewSet, basename='group')
router.register('', BookViewSet, basename='book')

urlpatterns = router.urls
