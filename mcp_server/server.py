# mcp_server/server.py
# Import the router
from google_auth import router as google_auth_router
from simple_oauth_server import router as simple_oauth_router
import os
import json
import time
from fastapi import FastAPI, HTTPException, Request, Depends, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware # Keep this
import httpx # For making HTTP requests to adk_app
from dotenv import load_dotenv # Keep this
from starlette.middleware.sessions import SessionMiddleware # Keep this for session management

# Adjusted import path: It's now inside the 'tools' package within mcp_server
from tools.weather_api_tool import WeatherAPITool

# Import jwt from jose
from jose import jwt, JWTError # <-- ADDED JWTError import here

GOOGLE_HOME_REPORT_STATE_URL = "https://homegraph.googleapis.com/v1/devices:reportState"

app = FastAPI(title="MCP Server - Multi-Agent Communication Protocol")

# Add CORS middleware (already present, just ensuring it's here)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or specify: ["http://localhost:3000"] etc.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY"))

app.include_router(google_auth_router)
app.include_router(simple_oauth_router)

# Load environment variables
load_dotenv()

# --- Configuration for OpenWeatherMap and ADK App ---
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")
if not OPENWEATHER_API_KEY:
    raise ValueError("FATAL ERROR: OPENWEATHER_API_KEY environment variable is not set for MCP Server.")

ADK_APP_URL = os.getenv("ADK_APP_URL", "http://adk_app:8000") # URL to adk_app service

# --- Internal ADK App Client ---
# Use httpx to make async requests to the adk_app for indoor status
adk_app_client = httpx.AsyncClient()

# --- Initialize Tools ---
weather_tool = WeatherAPITool(api_key=OPENWEATHER_API_KEY)

# --- Basic Authentication for Google Home (for development) ---
# This token needs to be configured in Google Actions Console later
# GOOGLE_HOME_AUTH_TOKEN is now effectively unused for OAuth-based fulfillment,
# but it might be used for direct testing of fulfillment URLs if you configure it.
# For now, we rely on the JWT access token issued by simple_oauth_server.
# GOOGLE_HOME_AUTH_TOKEN = os.getenv("GOOGLE_HOME_AUTH_TOKEN")
# if not GOOGLE_HOME_AUTH_TOKEN:
#     print("WARNING: GOOGLE_HOME_AUTH_TOKEN environment variable is not set. Google Home integration will not work without it.")

security = HTTPBearer()

# IMPORTANT: Get ACCESS_TOKEN_SECRET from the environment
ACCESS_TOKEN_SECRET = os.getenv('SECRET_KEY') # Assuming SECRET_KEY is used for JWT signing
if not ACCESS_TOKEN_SECRET:
    raise ValueError("FATAL ERROR: SECRET_KEY environment variable is not set. Cannot validate JWT tokens.")


async def verify_google_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    # print(f"Authentication attempt. Scheme: {credentials.scheme}, Token: {credentials.credentials}") # Uncomment for debugging
    if credentials.scheme != "Bearer":
        print(f"Authentication failed. Invalid scheme: {credentials.scheme}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication scheme",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = credentials.credentials # This is the JWT access token from Google
    
    try:
        # Decode and validate the JWT using your secret key
        payload = jwt.decode(token, ACCESS_TOKEN_SECRET, algorithms=["HS256"])
        
        # You might want to add more checks here, e.g.,
        # - check 'sub' (subject) to ensure it's a valid user ID (e.g., "user123")
        # - check 'exp' (expiration time) is handled by jwt.decode
        
        print(f"Token successfully decoded. User: {payload.get('sub')}")
        return True # Token is valid
        
    except JWTError as e: # <-- Catch JWTError specifically
        print(f"JWT validation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        print(f"An unexpected error occurred during token verification: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during authentication",
        )


# --- Existing API Endpoints ---

@app.get("/")
async def read_root():
    """Basic health check endpoint for the MCP server."""
    return {"message": "MCP Server (FastAPI) is running!"}

@app.get("/weather_current")
async def get_current_weather_endpoint(city: str):
    """
    Fetches current weather data for a given city using OpenWeatherMap 2.5 API (via Geocoding).
    """
    print(f"MCP Server: Received request for current weather for city='{city}'")
    current_weather_data_raw = await weather_tool.get_current_weather_2_5(city)

    if not current_weather_data_raw:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve current weather for {city}. Check MCP server logs for details from 2.5 API call.")

    # Parse the 2.5 API response structure as per your Postman output
    coord = current_weather_data_raw.get("coord", {})
    weather_list = current_weather_data_raw.get("weather", [{}])
    main_data = current_weather_data_raw.get("main", {})
    wind_data = current_weather_data_raw.get("wind", {})
    sys_data = current_weather_data_raw.get("sys", {})

    temp = main_data.get("temp")
    humidity = main_data.get("humidity")
    pressure = main_data.get("pressure")
    wind_speed = wind_data.get("speed")

    weather_info = weather_list[0].get("description", "N/A") if weather_list else "N/A"

    response_payload = {
        "city": city,
        "lat": coord.get("lat"),
        "lon": coord.get("lon"),
        "dt": current_weather_data_raw.get("dt"), # Unix timestamp of the data
        "temperature": temp,
        "humidity": humidity,
        "description": weather_info,
        "pressure": pressure,
        "wind_speed": wind_data.get("speed"), # Corrected from wind_speed to wind_data.get("speed") for consistency
        "country": sys_data.get("country")
    }
    print(f"MCP Server: Successfully prepared current weather data for {city}.")
    return JSONResponse(content=response_payload, status_code=200)

@app.get("/weather_historical")
async def get_historical_weather_endpoint(city: str, dt: int):
    """
    Fetches historical weather data for a given city at a specific Unix timestamp
    using the OpenWeatherMap 3.0 One Call API (Timemachine).
    """
    print(f"MCP Server: Received request for historical weather for city='{city}' at dt='{dt}'")
    historical_weather_data_raw = await weather_tool.get_historical_weather_one_call_3_0(city, dt)

    if not historical_weather_data_raw or "data" not in historical_weather_data_raw or len(historical_weather_data_raw["data"]) == 0:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve historical weather for {city} at {dt}. No data found or API error. Check MCP server logs for details.")

    hourly_data = historical_weather_data_raw["data"][0]

    temp = hourly_data.get("temp")
    humidity = hourly_data.get("humidity")
    pressure = hourly_data.get("pressure")
    wind_speed = hourly_data.get("wind_speed")

    weather_info = "N/A"
    if "weather" in hourly_data and len(hourly_data["weather"]) > 0:
        weather_info = hourly_data["weather"][0].get("description", "N/A")

    response_payload = {
        "city": city,
        "requested_dt": dt,
        "actual_data_dt": hourly_data.get("dt"), # The actual timestamp of the data point
        "temperature": temp,
        "humidity": humidity,
        "description": weather_info,
        "pressure": pressure,
        "wind_speed": wind_speed,
    }
    print(f"MCP Server: Successfully prepared historical weather data for {city}.")
    return JSONResponse(content=response_payload, status_code=200)

@app.get("/health")
async def health_check():
    """Endpoint for Docker healthchecks."""
    return {"status": "ok"}

# --- Google Home Fulfillment Endpoint ---
@app.post("/google_home/fulfillment")
async def google_home_fulfillment(request: Request, authenticated: bool = Depends(verify_google_token)):
    if not authenticated: # Should be caught by Depends, but for clarity
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    request_json = await request.json()
    print(f"Received Google Home request: {json.dumps(request_json, indent=2)}")

    request_id = request_json.get("requestId")
    inputs = request_json.get("inputs", [])
    response_payload = {"requestId": request_id, "payload": {}}

    for input_obj in inputs:
        intent = input_obj.get("intent")

        if intent == "action.devices.SYNC":
            response_payload["payload"] = await handle_sync_intent()
            break # Only one SYNC intent per request
        elif intent == "action.devices.QUERY":
            devices_to_query = input_obj.get("payload", {}).get("devices", [])
            response_payload["payload"] = await handle_query_intent(devices_to_query)
            break # Only one QUERY intent per request
        elif intent == "action.devices.EXECUTE":
            # For read-only sensors, EXECUTE intent is not applicable.
            # You would implement this if you had controllable devices (e.g., lights).
            print("Received EXECUTE intent, but not implemented for read-only sensors.")
            # Construct an error response or a success response for unsupported actions
            response_payload["payload"] = {"commands": [
                {"ids": [], "status": "ERROR", "errorCode": "actionNotSupported"}
            ]}
            break
        elif intent == "action.devices.DISCONNECT":
            # Handle account unlinking logic if needed (e.g., revoke tokens)
            print("Received DISCONNECT intent.")
            return JSONResponse({}, status_code=200) # Google expects an empty 200 OK for DISCONNECT

    print(f"Sending Google Home response: {json.dumps(response_payload, indent=2)}")
    return JSONResponse(response_payload)

async def handle_sync_intent():
    """Handles the SYNC intent to tell Google Home about available devices."""
    print("Handling SYNC intent...")
    # Define your virtual devices here
    devices = [
        {
            "id": "indoor-temperature",
            "type": "action.devices.types.SENSOR", # Or THERMOSTAT for more features if controllable
            "traits": ["action.devices.traits.TemperatureSetting"], # Use TemperatureSetting for read-only temp
            "name": {
                "defaultNames": ["My Home Indoor Temperature Sensor"],
                "name": "Indoor Temperature",
                "nicknames": ["room temp", "inside temp"]
            },
            "deviceInfo": {
                "manufacturer": "ADK IoT",
                "model": "SmartHome",
                "hwVersion": "1.0",
                "swVersion": "1.0"
            },
            "attributes": {
                "thermostatTemperatureUnit": "CELSIUS",
                "queryOnlyTemperatureSetting": True # Indicates it's a read-only sensor
            },
            "willReportState": True,
            "roomHint": "Living Room"
        },
        {
            "id": "indoor-humidity",
            "type": "action.devices.types.SENSOR",
            "traits": [
                "action.devices.traits.HumiditySetting"  # <--- CHANGED THIS TRAIT
            ],
            "name": {
                "defaultNames": ["My Indoor Humidity Sensor"],
                "name": "Indoor Humidity",
                "nicknames": ["indoor humidity", "inside humidity"]
            },
            "deviceInfo": {
                "manufacturer": "ADK IoT",
                "model": "SmartHome",
                "hwVersion": "1.0",
                "swVersion": "1.0"
            },
            "attributes": {  # <--- ADDED THIS ATTRIBUTES BLOCK
                "queryOnlyHumiditySetting": True
            },
            "willReportState": True,
            "roomHint": "Living Room"
        },
        {
            "id": "outdoor-temperature",
            "type": "action.devices.types.SENSOR",
            "traits": ["action.devices.traits.TemperatureSetting"],
            "name": {
                "defaultNames": ["My Outdoor Temperature Sensor"],
                "name": "Outdoor Temperature",
                "nicknames": ["outside temp", "weather temp"]
            },
            "deviceInfo": {
                "manufacturer": "ADK IoT",
                "model": "WeatherGuru",
                "hwVersion": "1.0",
                "swVersion": "1.0"
            },
            "attributes": {
                "thermostatTemperatureUnit": "CELSIUS",
                "queryOnlyTemperatureSetting": True
            },
            "willReportState": True,
            "roomHint": "Outside"
        },
        {
            "id": "outdoor-humidity",
            "type": "action.devices.types.SENSOR",
            "traits": [
                "action.devices.traits.HumiditySetting"  # <--- CHANGED THIS TRAIT
            ],
            "name": {
                "defaultNames": ["My Outdoor Humidity Sensor"],
                "name": "Outdoor Humidity",
                "nicknames": ["outdoor humidity", "outside humidity"]
            },
            "deviceInfo": {
                "manufacturer": "ADK IoT",
                "model": "WeatherGuru",
                "hwVersion": "1.0",
                "swVersion": "1.0"
            },
            "attributes": {  # <--- ADDED THIS ATTRIBUTES BLOCK
                "queryOnlyHumiditySetting": True
            },
            "willReportState": True,
            "roomHint": "Outside"
        },
    ]
    return {"agentUserId": "adk_user_123", "devices": devices} # Use a fixed user ID for testing

async def handle_query_intent(devices_to_query: list):
    """Handles the QUERY intent to provide current device states."""
    print(f"Handling QUERY intent for devices: {devices_to_query}")
    states = {}

    # Fetch indoor data from adk_app
    indoor_data = {}
    try:
        response = await adk_app_client.get(f"{ADK_APP_URL}/get_indoor_status/", timeout=5.0)
        response.raise_for_status()
        indoor_data = response.json()
        print(f"Fetched indoor data: {indoor_data}")
    except Exception as e:
        print(f"Error fetching indoor status from adk_app: {e}")

    # Fetch outdoor data from weather_tool
    outdoor_data = {}
    try:
        # For simplicity, we'll assume a default city (e.g., Bengaluru for your previous tests)
        # In a real app, you might get the city from device configuration or user context.
        outdoor_data_raw = await weather_tool.get_current_weather_2_5("Bengaluru") # Or dynamically choose city
        if outdoor_data_raw:
            outdoor_data = {
                "temperature": outdoor_data_raw["main"]["temp"],
                "humidity": outdoor_data_raw["main"]["humidity"],
                "description": outdoor_data_raw["weather"][0]["description"]
            }
            print(f"Fetched outdoor data: {outdoor_data}")
    except Exception as e:
        print(f"Error fetching outdoor status from weather_tool: {e}")

    for device in devices_to_query:
        device_id = device.get("id")
        current_state = {"online": True} # Assume devices are always online for now

        if device_id == "indoor-temperature":
            temp = indoor_data.get("temperature")
            if temp is not None and temp != "N/A":
                current_state["thermostatTemperatureAmbient"] = float(temp)
            else:
                current_state["online"] = False # Or handle as error
                print(f"Warning: Indoor temperature data not available for {device_id}")
        elif device_id == "indoor-humidity":
            humidity = indoor_data.get("humidity")
            if humidity is not None and humidity != "N/A":
                current_state["humidityAmbientPercent"] = float(humidity)
            else:
                current_state["online"] = False
                print(f"Warning: Indoor humidity data not available for {device_id}")
        elif device_id == "outdoor-temperature":
            temp = outdoor_data.get("temperature")
            if temp is not None:
                current_state["thermostatTemperatureAmbient"] = float(temp)
            else:
                current_state["online"] = False
                print(f"Warning: Outdoor temperature data not available for {device_id}")
        elif device_id == "outdoor-humidity":
            humidity = outdoor_data.get("humidity")
            if humidity is not None:
                current_state["humidityAmbientPercent"] = float(humidity)
            else:
                current_state["online"] = False
                print(f"Warning: Outdoor humidity data not available for {device_id}")
        # Add other device IDs here if you define more

        states[device_id] = current_state

    return {"agentUserId": "adk_user_123", "devices": states}

# This function would be called periodically
async def send_report_state_update(access_token: str):
    print("Starting periodic Report State check...") # This is the log I was looking for!

    # 1. Fetch latest indoor data
    indoor_data = {}
    try:
        response = await adk_app_client.get(f"{ADK_APP_URL}/get_indoor_status/", timeout=5.0)
        response.raise_for_status()
        indoor_data = response.json()
        print(f"DEBUG: Fetched indoor data for Report State: {indoor_data}") # Add this for debugging
    except Exception as e:
        print(f"Error fetching indoor status for Report State: {e}")
        return # Skip if data can't be fetched

    # 2. Fetch latest outdoor data (similar to handle_query_intent)
    outdoor_data = {}
    try:
        outdoor_data_raw = await weather_tool.get_current_weather_2_5("Bengaluru")
        if outdoor_data_raw:
            outdoor_data = {
                "temperature": outdoor_data_raw["main"]["temp"],
                "humidity": outdoor_data_raw["main"]["humidity"],
                "description": outdoor_data_raw["weather"][0]["description"]
            }
            print(f"DEBUG: Fetched outdoor data for Report State: {outdoor_data}")
    except Exception as e:
        print(f"Error fetching outdoor status for Report State: {e}")
        return

    # 3. Construct the report state payload
    payload = {
        "agentUserId": "adk_user_123", # Must match the ID from SYNC
        "payload": {
            "devices": {
                "states": {
                    "indoor-humidity": {
                        "online": True,
                        "humidityAmbientPercent": float(indoor_data.get("humidity", 0.0))
                    },
                    "indoor-temperature": {
                        "online": True,
                        "thermostatTemperatureAmbient": float(indoor_data.get("temperature", 0.0))
                    },
                    "outdoor-humidity": {
                        "online": True,
                        "humidityAmbientPercent": float(outdoor_data.get("humidity", 0.0))
                    },
                    "outdoor-temperature": {
                        "online": True,
                        "thermostatTemperatureAmbient": float(outdoor_data.get("temperature", 0.0))
                    }
                }
            }
        }
    }
    print(f"Sending Report State payload: {json.dumps(payload, indent=2)}") # This is another log I was looking for!

    # 4. Send to Google Home Report State API
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                GOOGLE_HOME_REPORT_STATE_URL,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {access_token}" # Requires a valid access token
                },
                json=payload
            )
            response.raise_for_status() # Raises an exception for 4xx/5xx responses
            print(f"Report State successful! Response: {response.json()}")
    except httpx.HTTPStatusError as e:
        print(f"Report State failed: HTTP error {e.response.status_code} - {e.response.text}")
    except Exception as e:
        print(f"An error occurred during Report State: {e}")

# Moved uvicorn.run to a standard if __name__ block
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=4000)