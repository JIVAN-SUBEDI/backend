from rest_framework import viewsets,status,permissions
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.db import transaction as db_transaction
from decimal import Decimal,InvalidOperation
import re
from customer.models import Customer,Transaction


from .models import Casino,PaymentMethod,Platforms
from .serializers import CasinoSerializer,PaymentMethodSerializer,PlatformsSerializer
from backend.permissions import IsSuperAdmin, IsAuthenticatedReadOnlySuperAdminWrite


class CasinoViewSet(viewsets.ModelViewSet):
    queryset = Casino.objects.all()
    serializer_class = CasinoSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]

class PaymentMethodViewSet(viewsets.ModelViewSet):
    queryset = PaymentMethod.objects.all()
    serializer_class = PaymentMethodSerializer
    permission_classes = [IsAuthenticated, IsAuthenticatedReadOnlySuperAdminWrite]
class PlatformsViewSet(viewsets.ModelViewSet):
    queryset = Platforms.objects.all()
    serializer_class = PlatformsSerializer
    permission_classes = [IsAuthenticated, IsAuthenticatedReadOnlySuperAdminWrite]

def username_to_fullname(username: str) -> str:
    parts = username.replace("-", " ").split()
    return " ".join(word.capitalize() for word in parts)


class DailyNoteParserView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def normalize_username(self, username: str) -> str:
        return re.sub(r"\s+", " ", username.strip()).lower()

    def get_casino_id(self, request):
        user = request.user

        if user.role == "super_admin":
            casino_id = request.data.get("casino")
            if not casino_id:
                return None, Response(
                    {"detail": "Casino is required for super admin imports."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return int(casino_id), None

        if user.role in ["casino_admin", "staff"]:
            if not user.casino_id:
                return None, Response(
                    {"detail": "User is not assigned to any casino."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return int(user.casino_id), None

        return None, Response(
            {"detail": "You do not have permission to import notes."},
            status=status.HTTP_403_FORBIDDEN,
        )

    def parse_line(self, raw_line: str):
        line = raw_line.strip()
        if not line:
            return {"raw": raw_line, "error": "Empty line."}

        normalized = re.sub(r"\s+", " ", line).strip()
        lowered = normalized.lower()
        tokens = normalized.split(" ")

        if len(tokens) < 4:
            return {
                "raw": raw_line,
                "error": "Invalid format. Expected: username amount platform payment_method",
            }

        raw_username = tokens[0].strip()
        if not raw_username:
            return {"raw": raw_line, "error": "Username is required."}

        username = self.normalize_username(raw_username)

        is_withdraw = False
        if (
            "cash out" in lowered
            or "cashout" in lowered
            or "withdrawal" in lowered
            or "withdraw" in lowered
        ):
            is_withdraw = True

        amount_token = None
        amount_index = None

        for idx, token in enumerate(tokens[1:], start=1):
            cleaned = token.replace("$", "").replace(",", "")
            if re.fullmatch(r"-?\d+(\.\d+)?", cleaned):
                amount_token = cleaned
                amount_index = idx
                break

        if amount_token is None:
            return {"raw": raw_line, "error": "Amount not found."}

        try:
            amount = Decimal(amount_token)
        except InvalidOperation:
            return {"raw": raw_line, "error": "Invalid amount."}

        if amount == 0:
            return {"raw": raw_line, "error": "Amount cannot be zero."}

        if amount < 0:
            is_withdraw = True
            amount = abs(amount)

        platform_token = None
        payment_token = None

        if amount_index is not None and len(tokens) > amount_index + 2:
            platform_token = tokens[amount_index + 1].strip().lower()
            payment_token = tokens[amount_index + 2].strip().lower()
        else:
            return {
                "raw": raw_line,
                "error": "Could not identify platform and payment method.",
            }

        return {
            "raw": raw_line,
            "username": username,  # normalized username
            "original_username": raw_username,  # optional, just for preview/debug
            "fullname": username_to_fullname(username),
            "amount": str(amount),
            "platform_token": platform_token,
            "payment_method_token": payment_token,
            "type": "withdraw" if is_withdraw else "deposit",
        }

    def post(self, request, *args, **kwargs):
        raw_text = request.data.get("raw_text", "")
        date_value = request.data.get("date")
        notes_prefix = request.data.get("notes_prefix", "")
        preview = request.data.get("preview", True)

        if not raw_text.strip():
            return Response(
                {"detail": "raw_text is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not date_value:
            return Response(
                {"detail": "date is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        casino_id, error_response = self.get_casino_id(request)
        if error_response:
            return error_response

        lines = [line for line in raw_text.splitlines() if line.strip()]
        parsed_rows = [self.parse_line(line) for line in lines]

        valid_rows = []
        error_rows = []

        for row in parsed_rows:
            if row.get("error"):
                error_rows.append(row)
                continue

            try:
                platform = Platforms.objects.get(name__iexact=row["platform_token"])
            except Platforms.DoesNotExist:
                error_rows.append(
                    {
                        **row,
                        "error": f"Platform '{row['platform_token']}' not found in database.",
                    }
                )
                continue

            try:
                payment_method = PaymentMethod.objects.get(
                    name__iexact=row["payment_method_token"]
                )
            except PaymentMethod.DoesNotExist:
                error_rows.append(
                    {
                        **row,
                        "error": f"Payment method '{row['payment_method_token']}' not found in database.",
                    }
                )
                continue

            row["platform_id"] = platform.id
            row["platform_name"] = platform.name
            row["payment_method_id"] = payment_method.id
            row["payment_method_name"] = payment_method.name
            valid_rows.append(row)

        if preview:
            total_deposits = sum(
                Decimal(r["amount"]) for r in valid_rows if r["type"] == "deposit"
            )
            total_withdrawals = sum(
                Decimal(r["amount"]) for r in valid_rows if r["type"] == "withdraw"
            )

            return Response(
                {
                    "preview": True,
                    "summary": {
                        "total_lines": len(lines),
                        "valid_lines": len(valid_rows),
                        "error_lines": len(error_rows),
                        "total_deposits": str(total_deposits),
                        "total_withdrawals": str(total_withdrawals),
                    },
                    "rows": valid_rows + error_rows,
                },
                status=status.HTTP_200_OK,
            )

        imported = []
        errors = []

        with db_transaction.atomic():
            for row in valid_rows:
                normalized_username = self.normalize_username(row["username"])

                # Check if same username exists in another casino
                existing_other_casino = Customer.objects.filter(
                    username__iexact=normalized_username
                ).exclude(casino_id=casino_id).first()

                if existing_other_casino:
                    errors.append(
                        {
                            **row,
                            "error": f"Username '{row['username']}' already exists in another casino.",
                        }
                    )
                    continue

                # Get existing customer in same casino, case-insensitive
                customer = Customer.objects.filter(
                    casino_id=casino_id,
                    username__iexact=normalized_username,
                ).order_by("id").first()

                created = False

                if not customer:
                    customer = Customer.objects.create(
                        username=normalized_username,
                        fullname=row["fullname"],
                        casino_id=casino_id,
                    )
                    created = True
                else:
                    # Optional: normalize old stored username if needed
                    if customer.username != normalized_username:
                        customer.username = normalized_username
                        customer.save(update_fields=["username"])

                tx = Transaction.objects.create(
                    customer=customer,
                    casino_id=casino_id,
                    added_by=request.user,
                    amount=Decimal(row["amount"]),
                    date=date_value,
                    notes=(notes_prefix or "").strip() or None,
                    type=row["type"],
                    platform_id=row["platform_id"],
                    payment_method_id=row["payment_method_id"],
                )

                imported.append(
                    {
                        "transaction_id": tx.id,
                        "customer_id": customer.id,
                        "username": customer.username,
                        "customer_created": created,
                        "type": tx.type,
                        "amount": str(tx.amount),
                        "platform": row["platform_name"],
                        "payment_method": row["payment_method_name"],
                    }
                )

        errors.extend(error_rows)

        return Response(
            {
                "preview": False,
                "summary": {
                    "total_lines": len(lines),
                    "imported_count": len(imported),
                    "error_count": len(errors),
                },
                "imported": imported,
                "errors": errors,
            },
            status=status.HTTP_200_OK,
        )
