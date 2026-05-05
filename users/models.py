from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

class Profile(models.Model):
    """ব্যবহারকারীর পদমর্যাদা (Role) সংরক্ষণের জন্য প্রোফাইল মডেল"""
    ROLE_CHOICES = [
        ('admin', 'Admin (অ্যাডমিন)'),
        ('manager', 'Manager (ম্যানেজার)'),
        ('staff', 'Staff (স্টাফ)'),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='staff')
    phone = models.CharField(max_length=15, blank=True)

    def __str__(self):
        return f"{self.user.username} - {self.role}"

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        # প্রথম ইউজারকে অটো অ্যাডমিন করে দেওয়া যেতে পারে, বাকিরা স্টাফ
        role = 'admin' if User.objects.count() == 1 else 'staff'
        Profile.objects.get_or_create(user=instance, role=role)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    if hasattr(instance, 'profile'):
        instance.profile.save()
