from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import BookViewSet, GroupViewSet, BookCSVImportView, BookCSVTemplateView

router = DefaultRouter()
router.register('groups', GroupViewSet, basename='group')
router.register('', BookViewSet, basename='book')

urlpatterns = router.urls + [
    path('csv-import/',   BookCSVImportView.as_view(),   name='book-csv-import'),
    path('csv-template/', BookCSVTemplateView.as_view(), name='book-csv-template'),
]
