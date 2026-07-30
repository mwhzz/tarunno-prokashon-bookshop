from django.conf import settings
from django.db import models


class BackupLog(models.Model):
    """পূর্ণ ডাটাবেজ ব্যাকআপ কখন নেওয়া হয়েছে তার রেকর্ড"""
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Backup at {self.created_at}"
