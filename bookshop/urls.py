from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('frontend.urls')),
    path('api/books/', include('books.urls')),
    path('api/stock/', include('stock.urls')),
    path('api/sales/', include('sales.urls')),
    path('api/expenses/', include('expenses.urls')),
    path('api/accounts/', include('accounts.urls')),
    path('api/users/', include('users.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
