from django.shortcuts import render, redirect
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages

# Dashboard
@login_required
def dashboard_view(request):
    return render(request, 'dashboard.html')

# POS
@login_required
def pos_view(request):
    return render(request, 'pos.html')

# Books
@login_required
def books_list_view(request):
    return render(request, 'books.html')

@login_required
def add_book_view(request):
    return render(request, 'add_book.html')

@login_required
def edit_book_view(request, id):
    return render(request, 'edit_book.html', {'book_id': id})

# Stock
@login_required
def stock_summary_view(request):
    return render(request, 'stock.html')

@login_required
def add_stock_view(request):
    return render(request, 'add_stock.html')

# Sales/Invoices
@login_required
def invoices_list_view(request):
    return render(request, 'invoices.html')

@login_required
def due_list_view(request):
    return render(request, 'due_list.html')

# Accounts & Reports
@login_required
def accounts_view(request):
    return render(request, 'accounts.html')

@login_required
def reports_view(request):
    return render(request, 'reports.html')

# Customers
@login_required
def wholesale_customers_view(request):
    return render(request, 'wholesale_customers.html')

# Expenses
@login_required
def expenses_view(request):
    return render(request, 'expenses.html')

@login_required
def expense_categories_view(request):
    return render(request, 'expense_categories.html')

@login_required
def cash_history_view(request):
    return render(request, 'cash_history.html')

@login_required
def manage_users_view(request):
    if not hasattr(request.user, 'profile') or request.user.profile.role != 'admin':
        return redirect('dashboard')
    return render(request, 'manage_users.html')

# Auth Views
def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
        
    if request.method == 'POST':
        u = request.POST.get('username')
        p = request.POST.get('password')
        remember = request.POST.get('remember')
        user = authenticate(username=u, password=p)
        if user:
            login(request, user)
            if remember:
                request.session.set_expiry(1209600) # 2 weeks
            else:
                request.session.set_expiry(0) # Browser close
            return redirect('dashboard')
        else:
            messages.error(request, 'ইউজারনেম বা পাসওয়ার্ড ভুল!')
            
    return render(request, 'login.html')

def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        u = request.POST.get('username')
        e = request.POST.get('email')
        p = request.POST.get('password')
        f = request.POST.get('first_name')
        l = request.POST.get('last_name')
        r = request.POST.get('role', 'staff')
        
        if User.objects.filter(username=u).exists():
            messages.error(request, 'এই ইউজারনেম অলরেডি ব্যবহার করা হয়েছে!')
        else:
            user = User.objects.create_user(username=u, email=e, password=p, first_name=f, last_name=l)
            # প্রোফাইল আপডেট (সিগন্যাল থেকে অলরেডি তৈরি হয়ে আছে, শুধু রোল চেঞ্জ হবে)
            if hasattr(user, 'profile'):
                user.profile.role = r
                user.profile.save()
            messages.success(request, 'একাউন্ট তৈরি হয়েছে! এখন লগইন করুন।')
            return redirect('login')
            
    return render(request, 'register.html')

def logout_view(request):
    logout(request)
    return redirect('login')
