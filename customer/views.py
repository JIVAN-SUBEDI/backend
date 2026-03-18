from rest_framework import permissions, viewsets,status as drf_status

from .models import Customer, Transaction
from .serializers import CustomerSerializer, TransactionSerializer
from rest_framework.pagination import PageNumberPagination
from django.db.models import Count
from rest_framework.response import Response
from rest_framework.views import APIView
from decimal import Decimal
from datetime import timedelta
from django.utils import timezone
class TransactionPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100

class CustomerViewSet(viewsets.ModelViewSet):
    serializer_class = CustomerSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        queryset = Customer.objects.select_related("casino").prefetch_related("transactions").annotate(
            txn_count=Count("transactions")
        )

        if user.role == "super_admin":
            return queryset

        if user.role in ["casino_admin", "staff"]:
            return queryset.filter(casino=user.casino)

        return Customer.objects.none()

    def perform_create(self, serializer):
        user = self.request.user
    
        if user.role in ["casino_admin", "staff"]:
            if not user.casino:
                raise ValidationError({"detail": "User is not assigned to any casino"})
            serializer.save(casino=user.casino)
    
      
        elif user.role == "super_admin":
            casino_id = self.request.data.get("casino")
    
            if not casino_id:
                raise ValidationError({"casino": "Casino is required for super admin"})
    
            serializer.save(casino_id=casino_id)
    
        else:
            raise ValidationError({"detail": "You do not have permission"})
            
    def perform_update(self, serializer):
        user = self.request.user
        instance = self.get_object()
    
        if user.role == "super_admin":
            casino_id = self.request.data.get("casino")
    
            # keep old casino if not provided
            if not casino_id:
                casino_id = instance.casino_id
    
            if not casino_id:
                raise ValidationError({"casino": "Casino is required for super admin"})
    
            serializer.save(casino_id=casino_id)
    
        elif user.role in ["casino_admin", "staff"]:
            if not user.casino_id:
                raise ValidationError({"detail": "User is not assigned to any casino"})
    
            # extra protection: cannot update another casino's record
            if instance.casino_id != user.casino_id:
                raise PermissionDenied("You cannot update records from another casino")
    
            serializer.save(casino_id=user.casino_id)
    
        else:
            raise PermissionDenied("You don't have permission to update this record")


class TransactionViewSet(viewsets.ModelViewSet):
    serializer_class = TransactionSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = TransactionPagination
    def get_queryset(self):
        user = self.request.user
        queryset = Transaction.objects.select_related(
            "customer", "casino", "platform", "payment_method", "added_by"
        ).order_by("-date", "-id")

        if user.role == "super_admin":
            pass

        elif user.role == "casino_admin":
            queryset = queryset.filter(casino=user.casino)

        else:
            queryset = queryset.filter(casino=user.casino, added_by=user)

        search = self.request.query_params.get("search")
        tx_type = self.request.query_params.get("type")

        if search:
            queryset = queryset.filter(customer__fullname__icontains=search)

        if tx_type in ["deposit", "withdraw"]:
            queryset = queryset.filter(type=tx_type)

        return queryset

    def perform_create(self, serializer):
        user = self.request.user
    
        if user.role == "super_admin":
            casino_id = self.request.data.get("casino")
            if not casino_id:
                raise ValidationError({"casino": "Casino is required for super admin"})
            serializer.save(added_by=user, casino_id=casino_id)
    
        elif user.role in ["casino_admin", "staff"]:
            if not user.casino_id:
                raise ValidationError({"detail": "User is not assigned to any casino"})
            serializer.save(added_by=user, casino=user.casino)
    
        else:
            raise PermissionDenied("You don't have permission to create transactions")

    def perform_update(self, serializer):
        user = self.request.user
        instance = self.get_object()
    
        if user.role == "super_admin":
            casino_id = self.request.data.get("casino")
    
            # keep old casino if not provided
            if not casino_id:
                casino_id = instance.casino_id
    
            if not casino_id:
                raise ValidationError({"casino": "Casino is required for super admin"})
    
            serializer.save(casino_id=casino_id)
    
        elif user.role in ["casino_admin", "staff"]:
            if not user.casino_id:
                raise ValidationError({"detail": "User is not assigned to any casino"})
    
            # extra protection: cannot update another casino's record
            if instance.casino_id != user.casino_id:
                raise PermissionDenied("You cannot update records from another casino")
    
            serializer.save(casino_id=user.casino_id)
    
        else:
            raise PermissionDenied("You don't have permission to update this record")
    
class CampaignSegmentsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self, request):
        user = request.user
        queryset = Customer.objects.select_related("casino").prefetch_related("transactions")

        if user.role == "super_admin":
            return queryset

        if user.role in ["casino_admin", "staff"]:
            return queryset.filter(casino=user.casino)

        return Customer.objects.none()

    def serialize_customer(self, customer, total_deposit=0, total_withdrawal=0, last_activity=None):
        return {
            "id": customer.id,
            "fullname": customer.fullname,
            "username": customer.username,
            "casino_name": customer.casino.name if customer.casino else "",
            "total_deposit": float(total_deposit),
            "total_withdrawal": float(total_withdrawal),
            "last_activity": last_activity,
        }

    def get(self, request, *args, **kwargs):
        queryset = self.get_queryset(request)
        now = timezone.now()
        regular_cutoff = now - timedelta(days=3)

        segments = {
            "vip_players": [],
            "regular_players": [],
            "high_deposit_players": [],
            "high_withdrawal_players": [],
            "inactive_players": [],
        }

        for customer in queryset:
            txns = list(customer.transactions.all())

            total_deposit = Decimal("0")
            total_withdrawal = Decimal("0")
            deposit_amounts = []
            last_activity = None

            for txn in txns:
                amount = Decimal(str(getattr(txn, "amount", 0) or 0))
                txn_type = str(getattr(txn, "transaction_type", "") or "").lower()
                txn_date = getattr(txn, "created_at", None)

                if txn_date and (last_activity is None or txn_date > last_activity):
                    last_activity = txn_date

                if txn_type == "deposit":
                    total_deposit += amount
                    deposit_amounts.append(amount)

                elif txn_type == "withdrawal":
                    total_withdrawal += amount

            item = self.serialize_customer(
                customer=customer,
                total_deposit=total_deposit,
                total_withdrawal=total_withdrawal,
                last_activity=last_activity,
            )

            # Regular player = has activity within last 3 days
            is_regular = last_activity is not None and last_activity >= regular_cutoff

            # VIP player = regular + all deposits > 50
            is_vip = (
                is_regular
                and len(deposit_amounts) > 0
                and all(amount > Decimal("50") for amount in deposit_amounts)
            )

            if is_vip:
                segments["vip_players"].append(item)

            if is_regular:
                segments["regular_players"].append(item)

            if total_deposit > Decimal("5000"):
                segments["high_deposit_players"].append(item)

            if total_withdrawal > Decimal("2000"):
                segments["high_withdrawal_players"].append(item)

            if not last_activity or last_activity < regular_cutoff:
                segments["inactive_players"].append(item)

        response = {
            "segments": {
                "vip_players": {
                    "name": "VIP Players",
                    "description": "Regular players whose every deposit is more than 50",
                    "count": len(segments["vip_players"]),
                    "players": segments["vip_players"],
                },
                "regular_players": {
                    "name": "Regular Players",
                    "description": "Players active within the last 3 days",
                    "count": len(segments["regular_players"]),
                    "players": segments["regular_players"],
                },
                "high_deposit_players": {
                    "name": "High Deposit Players",
                    "description": "Players with total deposits over 5000",
                    "count": len(segments["high_deposit_players"]),
                    "players": segments["high_deposit_players"],
                },
                "high_withdrawal_players": {
                    "name": "High Withdrawal Players",
                    "description": "Players with total withdrawals over 2000",
                    "count": len(segments["high_withdrawal_players"]),
                    "players": segments["high_withdrawal_players"],
                },
                "inactive_players": {
                    "name": "Inactive Players",
                    "description": "Players with no activity in the last 3 days",
                    "count": len(segments["inactive_players"]),
                    "players": segments["inactive_players"],
                },
            }
        }

        return Response(response, status=drf_status.HTTP_200_OK)
