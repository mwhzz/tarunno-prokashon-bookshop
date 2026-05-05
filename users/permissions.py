from rest_framework import permissions

class IsAdmin(permissions.BasePermission):
    """শুধুমাত্র অ্যাডমিন অ্যাকসেস"""
    def has_permission(self, request, view):
        return request.user.is_authenticated and hasattr(request.user, 'profile') and request.user.profile.role == 'admin'

class IsManager(permissions.BasePermission):
    """অ্যাডমিন এবং ম্যানেজার অ্যাকসেস"""
    def has_permission(self, request, view):
        return request.user.is_authenticated and hasattr(request.user, 'profile') and request.user.profile.role in ['admin', 'manager']

class IsStaff(permissions.BasePermission):
    """যেকোনো লগইন করা স্টাফ"""
    def has_permission(self, request, view):
        return request.user.is_authenticated and hasattr(request.user, 'profile')
