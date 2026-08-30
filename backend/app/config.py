"""App config - Company location Adelaide, preserve ABN + Paddle"""
import os
COMPANY_LOCATION = os.environ.get("COMPANY_LOCATION", "Adelaide, South Australia")
COMPANY_CITY = "Adelaide"
COMPANY_STATE = "South Australia"
COMPANY_LOCATION_FULL = "Adelaide, South Australia"
ABN = "60 578 933 517"
SALES_TAX = "60578933517"
PADDLE_TOKEN = "live_0fcc154c8d0a7d55e605623a72f"
# Location helpers
def get_location():
    return COMPANY_LOCATION
