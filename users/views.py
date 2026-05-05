from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.contrib.auth.models import User
from .serializers import UserSerializer
from .permissions import IsAdmin

class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all().select_related('profile').order_by('id')
    serializer_class = UserSerializer
    permission_classes = [IsAdmin]
    filter_backends = [filters.SearchFilter]
    search_fields = ['username', 'email', 'profile__phone']

    @action(detail=False, methods=['get'])
    def me(self, request):
        """নিজের প্রোফাইল দেখার জন্য"""
        serializer = UserSerializer(request.user)
        return Response(serializer.data)

    @action(detail=False, methods=['post'])
    def change_password(self, request):
        """পাসওয়ার্ড পরিবর্তনের জন্য"""
        from django.contrib.auth.hashers import make_password
        current_password = request.data.get('current_password')
        new_password = request.data.get('new_password')
        
        if not current_password or not new_password:
            return Response({'error': 'All fields are required'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Verify current password
        if not request.user.check_password(current_password):
            return Response({'error': 'Incorrect current password'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Change password
        request.user.password = make_password(new_password)
        request.user.save()
        
        return Response({'success': 'Password changed successfully'})
