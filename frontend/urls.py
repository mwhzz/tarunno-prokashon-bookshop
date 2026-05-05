from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard_view, name='dashboard'),
    path('pos/', views.pos_view, name='pos'),
    
    # Books
    path('books/', views.books_list_view, name='books_list'),
    path('books/add/', views.add_book_view, name='add_book'),
    path('books/edit/<int:id>/', views.edit_book_view, name='edit_book'),
    
    # Stock
    path('stock/', views.stock_summary_view, name='stock_summary'),
    path('stock/add/', views.add_stock_view, name='add_stock'),
    
    # Sales
    path('sales/', views.invoices_list_view, name='invoices_list'),
    path('sales/due/', views.due_list_view, name='due_list'),
    
    # Accounts & Reports
    path('accounts/', views.accounts_view, name='accounts'),
    path('reports/', views.reports_view, name='reports'),
    
    # Customers
    path('customers/wholesale/', views.wholesale_customers_view, name='wholesale_customers'),
    
    # Expenses
    path('expenses/', views.expenses_view, name='expenses'),
    path('expenses/categories/', views.expense_categories_view, name='expense_categories'),
    
    # Cash History
    path('accounts/history/', views.cash_history_view, name='cash_history'),
    
    # User Management
    path('users/manage/', views.manage_users_view, name='manage_users'),
    
    # Auth
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('register/', views.register_view, name='register'),
]
