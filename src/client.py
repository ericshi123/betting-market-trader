import os
from dotenv import load_dotenv

load_dotenv()

CLOB_HOST = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
GAMMA_HOST = os.getenv("GAMMA_HOST", "https://gamma-api.polymarket.com")


def get_clob_client(authenticated: bool = False):
    """
    Return a py-clob-client ClobClient instance.
    Pass authenticated=True to enable order placement (requires .env credentials).
    """
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds

    if authenticated:
        api_key = os.getenv("POLY_API_KEY")
        api_secret = os.getenv("POLY_API_SECRET")
        passphrase = os.getenv("POLY_PASSPHRASE")
        private_key = os.getenv("POLY_PRIVATE_KEY")

        if not all([api_key, api_secret, passphrase, private_key]):
            raise EnvironmentError(
                "Authenticated mode requires POLY_API_KEY, POLY_API_SECRET, "
                "POLY_PASSPHRASE, and POLY_PRIVATE_KEY in .env"
            )

        creds = ApiCreds(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=passphrase,
        )
        return ClobClient(CLOB_HOST, key=private_key, creds=creds)

    # Unauthenticated — read-only, no key needed
    return ClobClient(CLOB_HOST)
