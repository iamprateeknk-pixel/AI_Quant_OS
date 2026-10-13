"""Operator authentication and backend-owned session HTTP boundary."""

from datetime import datetime
import hmac
import sqlite3
from typing import Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Response

from ai_quant_os.operator_session_contracts import (
    OperatorSession,
    SessionInventory,
    SessionRevocationReceipt,
)
from ai_quant_os.operator_session_store import (
    OperatorSessionStore,
    SessionHeadChanged,
    SessionTargetMissing,
)
from pydantic import BaseModel, ConfigDict, Field, UUID4


class SessionRevocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    target_session_id: UUID4
    expected_head: str = Field(pattern=r"^[a-f0-9]{64}$")


class OperatorAuthorization:
    def __init__(
        self,
        token: str | None,
        store: OperatorSessionStore | None,
        clock: Callable[[], datetime],
        admission: Callable[[], None],
    ):
        self._token, self._store = token, store
        self._clock, self._admission = clock, admission
        self._epoch = (
            hmac.digest(token.encode(), b"ai-quant-operator-session-v1", "sha256").hex()
            if token
            else "0" * 64
        )

    def _available_store(self) -> OperatorSessionStore:
        if self._store is None:
            raise HTTPException(503, "Operator session journal unavailable")
        return self._store

    def _credential(self, supplied: str | None):
        if not self._token:
            raise HTTPException(503, "Operator commands are disabled")
        if not supplied or not hmac.compare_digest(supplied.encode(), self._token.encode()):
            raise HTTPException(401, "Invalid operator credential")

    def require_direct(self, x_operator_token: str | None = Header(default=None)):
        self._credential(x_operator_token)
        self._admission()

    def require_session(
        self, x_operator_session: str | None = Header(default=None)
    ) -> OperatorSession:
        grant = self.authenticate_session(x_operator_session)
        self._admission()
        return grant

    def authenticate_session(self, token: str | None) -> OperatorSession:
        """Revalidate an already admitted session without reacquiring its admission locks."""
        if not self._token:
            raise HTTPException(503, "Operator commands are disabled")
        if not token:
            raise HTTPException(401, "Operator session required")
        try:
            grant = self._available_store().authenticate(token, self._clock(), self._epoch)
        except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
            raise HTTPException(503, "Operator session journal could not be verified") from error
        if grant is None:
            raise HTTPException(401, "Operator session expired or invalid")
        return grant

    def require_operator(
        self,
        x_operator_token: str | None = Header(default=None),
        x_operator_session: str | None = Header(default=None),
    ):
        if x_operator_token is not None:
            self.require_direct(x_operator_token)
        else:
            self.require_session(x_operator_session)

    def require_control(
        self,
        x_operator_token: str | None = Header(default=None),
        x_operator_session: str | None = Header(default=None),
        x_operator_reauth: str | None = Header(default=None),
    ):
        self.require_operator(x_operator_token, x_operator_session)
        if x_operator_token is None:
            self._credential(x_operator_reauth)

    def install(self, app: FastAPI):
        @app.get(
            "/api/v1/operator/sessions",
            dependencies=[Depends(self.require_session)],
            response_model=SessionInventory,
        )
        def session_inventory(response: Response, x_operator_session: str = Header()):
            try:
                view = SessionInventory.model_validate(
                    self._available_store().administrative_view(
                        x_operator_session, self._clock(), self._epoch
                    )
                )
            except PermissionError as error:
                raise HTTPException(401, "Operator session expired or invalid") from error
            except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
                raise HTTPException(503, "Session inventory could not be verified") from error
            response.headers["Cache-Control"] = "no-store"
            return view

        @app.post(
            "/api/v1/operator/sessions/revoke",
            dependencies=[Depends(self.require_session)],
            response_model=SessionRevocationReceipt,
        )
        def revoke_selected(
            command: SessionRevocation,
            response: Response,
            x_operator_session: str = Header(),
            x_operator_reauth: str | None = Header(default=None),
        ):
            self._credential(x_operator_reauth)
            try:
                result = SessionRevocationReceipt.model_validate(
                    self._available_store().administrative_revoke(
                        x_operator_session,
                        str(command.target_session_id),
                        command.expected_head,
                        self._clock(),
                        self._epoch,
                    )
                )
            except PermissionError as error:
                raise HTTPException(401, "Operator session expired or invalid") from error
            except SessionHeadChanged as error:
                raise HTTPException(
                    409, "Session inventory changed; refresh before selecting a session"
                ) from error
            except SessionTargetMissing as error:
                raise HTTPException(404, "Session is no longer in the current inventory") from error
            except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
                raise HTTPException(503, "Session revocation could not be verified") from error
            response.headers["Cache-Control"] = "no-store"
            return result

        @app.post(
            "/api/v1/operator/sessions",
            status_code=201,
            dependencies=[Depends(self.require_direct)],
        )
        def issue(response: Response):
            try:
                token, grant = self._available_store().issue(self._clock(), self._epoch)
            except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
                raise HTTPException(503, "Operator session could not be issued") from error
            response.headers["Cache-Control"] = "no-store"
            return {**grant.public(), "session_token": token}

        @app.get("/api/v1/operator/session")
        def status(response: Response, grant: OperatorSession = Depends(self.require_session)):
            response.headers["Cache-Control"] = "no-store"
            return grant.public()

        @app.delete("/api/v1/operator/session", dependencies=[Depends(self.require_session)])
        def revoke(response: Response, x_operator_session: str = Header()):
            try:
                self._available_store().revoke(x_operator_session, self._clock(), self._epoch)
            except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
                raise HTTPException(503, "Operator session could not be revoked") from error
            response.headers["Cache-Control"] = "no-store"
            return {"mode": "PAPER", "status": "REVOKED"}
