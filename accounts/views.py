from rest_framework import generics, permissions, viewsets,status
from rest_framework.exceptions import PermissionDenied
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework.response import Response
from .models import User
from .serializers import (
    UserSerializer,
    CreateUserSerializer,
    CustomTokenObtainPairSerializer,
    ChangePasswordSerializer,
    UpdateProfileSerializer
)
from backend.permissions import IsSuperAdminOrCasinoAdmin


class LoginView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer


class MeView(generics.RetrieveAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


class UserViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, IsSuperAdminOrCasinoAdmin]

    def get_queryset(self):
        user = self.request.user

        if user.role == "super_admin":
            return User.objects.select_related("casino").all()

        if user.role == "casino_admin":
            return User.objects.select_related("casino").filter(casino=user.casino)

        return User.objects.none()

    def get_serializer_class(self):
        if self.action == "create":
            return CreateUserSerializer
        return UserSerializer

    def perform_create(self, serializer):
        serializer.save()

    def perform_update(self, serializer):
        current_user = self.request.user
        target_user = self.get_object()

        if current_user.role == "casino_admin":
            if target_user.role != "staff" or target_user.casino != current_user.casino:
                raise PermissionDenied("You can only update your own casino staff.")
            serializer.save(casino=current_user.casino)
            return

        serializer.save()

    def destroy(self, request, *args, **kwargs):
        current_user = request.user
        target_user = self.get_object()

        if current_user.role == "casino_admin":
            if target_user.role != "staff" or target_user.casino != current_user.casino:
                raise PermissionDenied("You can only delete your own casino staff.")

        return super().destroy(request, *args, **kwargs)
class ChangePasswordView(generics.GenericAPIView):
    serializer_class = ChangePasswordSerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        current_password = serializer.validated_data["current_password"]
        new_password = serializer.validated_data["new_password"]

        if not user.check_password(current_password):
            return Response(
                {"current_password": ["Current password is incorrect."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_password)
        user.save(update_fields=["password"])

        return Response(
            {"detail": "Password changed successfully."},
            status=status.HTTP_200_OK,
        )
class UpdateProfileView(generics.UpdateAPIView):
    serializer_class = UpdateProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user