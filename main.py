# -*- coding: utf-8 -*-
import time
import threading
import speedtest      # Provider 1 (speedtest-cli)
import requests
import json
from datetime import datetime, timedelta
import os
import traceback
# Removed: import fastdotcom2
from typing import List, Dict, Any, Optional, Tuple, Callable # For type hints
import sys # For exiting on critical errors
from pathlib import Path # Use pathlib for easier path manipulation

# --- Configuration ---

def get_log_directory() -> Path:
    """Gets the log directory path from the user or uses a default."""
    default_dir = Path(".").resolve() # Default to current directory, resolved to absolute path
    log_dir_input = input(f'Enter log directory path (leave empty for default: {default_dir}): ')
    if not log_dir_input:
        log_dir = default_dir
    else:
        log_dir = Path(log_dir_input)

    # Attempt to create the directory early to catch permission issues
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        # Use resolve() to get the absolute path
        return log_dir.resolve()
    except OSError as e:
        print(f"\nCRITICAL ERROR: Could not create log directory '{log_dir}': {e}", file=sys.stderr)
        print("Please ensure the path is correct and you have write permissions.", file=sys.stderr)
        sys.exit(1) # Exit if we can't create the log directory

LOG_DIRECTORY: Path = get_log_directory()

CHECK_CYCLE_INTERVAL: int = 40  # Seconds (e.g., 120 = 2 minutes)
RETRY_DELAY: int = 30          # Seconds
MAX_RETRIES: int = 3
REQUEST_TIMEOUT: int = 40      # Seconds (Increased slightly for download/ping tests)
USER_AGENT: str = 'InternetMonitorScript/1.5' # Slightly updated agent (reflecting API list change)

# IP Check Sites Configuration
# *** MODIFIED: Added 3 more free services providing location info ***
IP_CHECK_SITES: List[Dict[str, Any]] = [
    # Existing ones providing location:
    {'name': 'ipinfo.io', 'url': 'https://ipinfo.io/json', 'key': 'ip', 'city_key': 'city', 'country_key': 'country'},
    {'name': 'ip-api.com', 'url': 'http://ip-api.com/json', 'key': 'query', 'city_key': 'city', 'country_key': 'country'}, # Note: 'query' key for IP
    {'name': 'geolocation-db.com', 'url': 'https://geolocation-db.com/json/', 'key': 'IPv4', 'city_key': 'city', 'country_key': 'country_name'}, # Note: 'IPv4' and 'country_name'

    # Newly added free services providing location:
    {'name': 'freegeoip.app', 'url': 'https://freegeoip.app/json/', 'key': 'ip', 'city_key': 'city', 'country_key': 'country_name'}, # Note: 'country_name'
    {'name': 'extreme-ip-lookup.com', 'url': 'https://extreme-ip-lookup.com/json/', 'key': 'query', 'city_key': 'city', 'country_key': 'country'}, # Note: 'query' key for IP

    # Existing ones providing IP only (kept for fallback/variety):
    # {'name': 'ipify', 'url': 'https://api.ipify.org?format=json', 'key': 'ip'},
    # {'name': 'myexternalip', 'url': 'https://myexternalip.com/json', 'key': 'ip'},
    # {'name': 'seeip.org', 'url': 'https://api.seeip.org/jsonip', 'key': 'ip'},
]
# --- End IP Check Sites Configuration ---


# Speed Test Provider Configuration
SPEED_TEST_PROVIDERS: List[str] = ['speedtest']

# --- Custom Speed Test Provider Details ---
# Store configuration for custom providers here
CUSTOM_PROVIDERS_CONFIG: Dict[str, Dict[str, Any]] = {
'Tele2': {
    'name': 'Tele2',
    'ping_url': 'http://speedtest.tele2.net',  # URL for ping test
    'download_url': 'http://speedtest.tele2.net/10MB.zip',  # URL for download file
    'file_size_bytes': 10 * 1024 * 1024,  # 10 MiB = 10 * 1024 * 1024 bytes
    'upload_url': 'ftp://speedtest.tele2.net/upload',  # FTP URL for upload test (not currently used in test logic)
    'upload_size_bytes': 10 * 1024 * 1024  # 10 MiB for upload test (not currently used in test logic)
},

    # Add other custom providers here in the future
}


# --- End Configuration ---


# --- File Paths ---
TEXT_LOG_FILE: Path = LOG_DIRECTORY / "internet_log.txt"
JSON_LOG_FILE: Path = LOG_DIRECTORY / "internet_log.json"
ERROR_LOG_FILE: Path = LOG_DIRECTORY / "error_log.txt"
ERROR_JSON_LOG_FILE: Path = LOG_DIRECTORY / "error_log.json"
# --- End File Paths ---


# --- Locks ---
text_log_lock = threading.Lock()
json_log_lock = threading.Lock()
error_text_log_lock = threading.Lock()
error_json_log_lock = threading.Lock()
# --- End Locks ---


# --- Utility Functions ---
def get_timestamp() -> str:
    """Returns the current timestamp in a standard format."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
# --- End Utility Functions ---


# --- Logging Functions (Unchanged from previous step) ---
# Helper for writing JSON logs
def _write_json_log(file_path: Path, entry: Dict[str, Any], lock: threading.Lock):
    """Appends a dictionary entry to a JSON log file."""
    with lock:
        data: List[Dict[str, Any]] = []
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            if file_path.exists() and file_path.stat().st_size > 0:
                 with open(file_path, "r", encoding='utf-8') as f:
                    try:
                        content = json.load(f)
                        if isinstance(content, list): data = content
                        else:
                           msg = f"Warning: {file_path.name} content invalid. Reinitializing."
                           print(f"[{get_timestamp()}] {msg}")
                           log_to_text(msg)
                           data = []
                    except json.JSONDecodeError:
                        msg = f"Warning: Could not decode JSON from {file_path.name}. Reinitializing."
                        print(f"[{get_timestamp()}] {msg}")
                        log_to_text(msg)
                        data = []
            entry.setdefault("timestamp", get_timestamp())
            data.append(entry)
            with open(file_path, "w", encoding='utf-8') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            ts = get_timestamp()
            err_msg = f"CRITICAL ERROR processing JSON log {file_path.name}: {e}"
            print(f"[{ts}] {err_msg}", file=sys.stderr)
            traceback.print_exc()
            try:
                error_info = f"{err_msg}\n\tOriginal entry: {entry}\n{traceback.format_exc()}"
                with error_text_log_lock:
                    with open(ERROR_LOG_FILE, "a", encoding='utf-8') as f_err:
                         f_err.write(f"[{ts}] {error_info}\n")
            except Exception as log_err_e:
                 print(f"[{ts}] CRITICAL FAILURE: Could not write to text error log: {log_err_e}", file=sys.stderr)

def log_to_text(message: str):
    """Appends a message to the main text log file."""
    ts = get_timestamp()
    log_line = f"[{ts}] {message}\n" if not message.strip().startswith('[') else f"{message}\n"
    with text_log_lock:
        try:
            TEXT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(TEXT_LOG_FILE, "a", encoding='utf-8') as f:
                f.write(log_line)
        except Exception as e:
            print(f"[{ts}] CRITICAL ERROR writing to text log {TEXT_LOG_FILE.name}: {e}\n\tOriginal message: {message}", file=sys.stderr)
            traceback.print_exc()
            try: log_error(f"CRITICAL ERROR writing to text log {TEXT_LOG_FILE.name}: {e}. Original message: {message}")
            except: pass

def log_to_json(entry: Dict[str, Any]):
    """Appends a dictionary entry to the main JSON log file."""
    _write_json_log(JSON_LOG_FILE, entry, json_log_lock)

def log_error(message: str):
    """Appends a persistent error message to the text Error log file."""
    ts = get_timestamp()
    with error_text_log_lock:
        try:
            ERROR_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(ERROR_LOG_FILE, "a", encoding='utf-8') as f:
                f.write(f"[{ts}] {message}\n")
        except Exception as e:
            print(f"[{ts}] CRITICAL ERROR writing to text error log {ERROR_LOG_FILE.name}: {e}\n\tOriginal error message: {message}", file=sys.stderr)
            traceback.print_exc()

def log_error_to_json(error_data: Dict[str, Any]):
    """Appends a structured error dictionary to the JSON error log file."""
    _write_json_log(ERROR_JSON_LOG_FILE, error_data, error_json_log_lock)

def log_persistent_error(operation: str, initial_exception: Exception, last_exception: Exception, retries: int):
    """Logs errors that persisted after retries to both text and JSON error logs."""
    error_timestamp = get_timestamp()
    # Correctly capture traceback from the context of the *last* exception
    tb_str = "".join(traceback.format_exception(type(last_exception), last_exception, last_exception.__traceback__))

    error_msg_text = (
        f"PERSISTENT ERROR ({operation}): Failed after {retries} retries. "
        f"Initial error: ({type(initial_exception).__name__}) {initial_exception}. "
        f"Last error: ({type(last_exception).__name__}) {last_exception}\n{tb_str}"
    )
    log_error(error_msg_text)
    print(f"    [{error_timestamp}] PERSISTENT ERROR logged for {operation}. Check error logs.")

    error_data_json = {
        "timestamp": error_timestamp, "operation": operation, "status": f"Failed after {retries} retries",
        "initial_error_type": type(initial_exception).__name__, "initial_error_message": str(initial_exception),
        "last_error_type": type(last_exception).__name__, "last_error_message": str(last_exception),
        "traceback": tb_str
    }
    log_error_to_json(error_data_json)
# --- End Logging Functions ---


# --- Core Check Functions ---

# perform_ip_check (Unchanged from previous step - handles location dynamically)
def perform_ip_check(site_info: Dict[str, Any]) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Performs IP check using the specified site info.
    Attempts to extract city and country if the provider supports it.
    Returns Tuple (ip_address, city, country) or raises an appropriate Exception.
    City and Country will be None if not available from the provider or in the response.
    """
    url = site_info['url']
    ip_key = site_info['key']
    site_name = site_info['name']
    # Get optional location keys from config (will be None if not defined)
    city_key = site_info.get('city_key')
    country_key = site_info.get('country_key')

    headers = {'User-Agent': USER_AGENT}
    operation = f"IP Check via {site_name}"
    ip_address: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None

    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT, headers=headers)
        response.raise_for_status() # Raises HTTPError for bad responses (4xx or 5xx)
        ip_data = response.json()

        # 1. Extract IP Address (Mandatory)
        current_ip = ip_data.get(ip_key)
        if not current_ip:
            raise ValueError(f"IP key '{ip_key}' not found or value is empty in response from {site_name}. Response: {ip_data}")
        if not isinstance(current_ip, str) or not any(c in current_ip for c in '.:') or len(current_ip) < 7:
             raise ValueError(f"Value '{current_ip}' for key '{ip_key}' from {site_name} is not a valid IP format.")
        ip_address = current_ip # IP is confirmed valid

        # 2. Extract Location (Optional, based on config and response)
        if city_key:
            city = ip_data.get(city_key)
            if city is not None and not isinstance(city, str): # Allow None, but check type if present
                log_to_text(f"Warning: Unexpected type for city key '{city_key}' from {site_name}. Got: {type(city)}. Ignoring.")
                city = None # Reset if type is wrong
            elif city == "": # Treat empty string as None
                 city = None

        if country_key:
            country = ip_data.get(country_key)
            if country is not None and not isinstance(country, str): # Allow None, but check type if present
                log_to_text(f"Warning: Unexpected type for country key '{country_key}' from {site_name}. Got: {type(country)}. Ignoring.")
                country = None # Reset if type is wrong
            elif country == "": # Treat empty string as None
                 country = None

        # Return IP and whatever location info we could get
        return ip_address, city, country

    except requests.exceptions.Timeout:
         raise TimeoutError(f"{operation} timed out after {REQUEST_TIMEOUT}s") from None
    except requests.exceptions.HTTPError as e:
         # Log specific HTTP errors
         raise ConnectionError(f"HTTP error during {operation}: {e.response.status_code} {e.response.reason}") from e
    except requests.exceptions.RequestException as e:
        # Catch other potential network errors (DNS, ConnectionRefused, etc.)
        raise ConnectionError(f"Network error during {operation}: {e}") from e
    except json.JSONDecodeError as e:
         # Handle cases where the response isn't valid JSON
         response_text = response.text[:200] if 'response' in locals() and hasattr(response, 'text') else "N/A"
         raise ValueError(f"JSON decode error during {operation}: {e}. Response (partial): '{response_text}'") from e
    except ValueError as e:
         # Re-raise specific ValueErrors (e.g., from IP validation or missing keys)
         raise
    except Exception as e:
        # Log any other unexpected errors
        tb_str = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        log_error(f"Unexpected error during {operation}: {e}\n{tb_str}")
        raise RuntimeError(f"Unexpected error during {operation}: {e}") from e


# perform_speed_test (Unchanged from previous step)
def perform_speed_test(provider_name: str) -> Tuple[float, float, float]:
    """
    Performs speed test using the specified provider ('speedtest' or custom).
    Returns (download_mbps, upload_mbps, ping_ms) or raises Exception.
    Returns 0.0 for metrics that are unavailable or failed.
    """
    operation = f"Speed Test via {provider_name}"
    download_mbps: float = 0.0
    upload_mbps: float = 0.0
    ping_ms: float = 0.0
    headers = {'User-Agent': USER_AGENT} # Common headers

    try:
        if provider_name == 'speedtest':
            # --- Speedtest-cli (Official) ---
            st = speedtest.Speedtest(secure=True, timeout=REQUEST_TIMEOUT + 10) # Slightly longer timeout for speedtest internal steps
            # print(f"      {provider_name}: Getting best server...")
            st.get_best_server() # This can take time
            # print(f"      {provider_name}: Testing download...")
            st.download(threads=None)
            # print(f"      {provider_name}: Testing upload...")
            st.upload(threads=None)
            results = st.results.dict()
            # print(f"      {provider_name}: Test complete.")

            down_bps = results.get("download")
            up_bps = results.get("upload")
            ping_res = results.get("ping")

            download_mbps = (down_bps / 1_000_000) if down_bps is not None else 0.0
            upload_mbps = (up_bps / 1_000_000) if up_bps is not None else 0.0
            ping_ms = float(ping_res) if ping_res is not None else 0.0

            if download_mbps <= 0 and upload_mbps <= 0 and ping_ms <= 0:
                 msg = f"Warning: {operation} returned zero/invalid results for all metrics. Raw: {results}"
                 print(f"    {msg}")
                 log_to_text(msg)
            elif ping_ms < 0:
                 log_to_text(f"Warning: {operation} ping measurement failed/invalid ({ping_ms}ms). Raw: {results}")
                 ping_ms = 0.0 # Treat negative ping as failure/unavailable

        elif provider_name in CUSTOM_PROVIDERS_CONFIG:
            # --- Custom Provider Logic (e.g., Tele2) ---
            config = CUSTOM_PROVIDERS_CONFIG[provider_name]
            ping_url = config.get('ping_url')
            download_url = config.get('download_url')
            file_size_bytes = config.get('file_size_bytes')
            # upload_url = config.get('upload_url') # Not used in this simple example

            # 1. Ping Test (Simple HEAD request timing)
            if ping_url:
                # print(f"      {provider_name}: Measuring ping to {ping_url}...")
                ping_start_time = time.monotonic()
                try:
                    # Use HEAD for efficiency, fallback to GET if HEAD is disallowed/fails
                    try:
                         response = requests.head(ping_url, timeout=REQUEST_TIMEOUT / 2, headers=headers) # Shorter timeout for ping
                         response.raise_for_status()
                    except requests.exceptions.RequestException:
                         # Fallback to GET if HEAD fails
                         response = requests.get(ping_url, timeout=REQUEST_TIMEOUT / 2, headers=headers, stream=True) # stream=True to only get headers
                         response.raise_for_status()
                    ping_duration = time.monotonic() - ping_start_time
                    ping_ms = ping_duration * 1000 # Convert to milliseconds
                except (requests.exceptions.RequestException, ConnectionError, TimeoutError) as e:
                    print(f"      {provider_name}: Ping test failed: {e}")
                    log_to_text(f"Warning: {operation} ping test failed: {e}")
                    ping_ms = 0.0 # Indicate failure explicitly
            else:
                print(f"      {provider_name}: No ping URL configured.")
                ping_ms = 0.0 # Indicate not performed

            # 2. Download Test
            if download_url and file_size_bytes:
                # print(f"      {provider_name}: Measuring download from {download_url} ({file_size_bytes / (1024*1024):.1f} MiB)...")
                download_start_time = time.monotonic()
                try:
                    response = requests.get(download_url, timeout=REQUEST_TIMEOUT * 2, headers=headers, stream=True) # Longer timeout, stream=True is good practice
                    response.raise_for_status()

                    actual_size = 0
                    for chunk in response.iter_content(chunk_size=8192):
                        actual_size += len(chunk)

                    if actual_size == 0:
                        raise ValueError("Downloaded 0 bytes.")

                    download_duration = time.monotonic() - download_start_time
                    if download_duration > 0:
                         # Use actual downloaded size for calculation
                         speed_bps = (actual_size * 8) / download_duration
                         download_mbps = speed_bps / 1_000_000
                    else:
                         print(f"      {provider_name}: Download duration was zero or negative. Cannot calculate speed.")
                         download_mbps = 0.0 # Indicate calculation error

                except (requests.exceptions.RequestException, ConnectionError, TimeoutError, ValueError) as e:
                    print(f"      {provider_name}: Download test failed: {e}")
                    log_to_text(f"Warning: {operation} download test failed: {e}")
                    download_mbps = 0.0 # Indicate failure
            else:
                print(f"      {provider_name}: No download URL or file size configured.")
                download_mbps = 0.0 # Indicate not performed

            # 3. Upload Test (Placeholder - Not implemented for Tele2 example)
            upload_mbps = 0.0 # Set explicitly as not tested
            print(f"      {provider_name}: Upload test not configured/skipped.")


        else:
            raise ValueError(f"Unknown speed test provider configured: {provider_name}")

        # Ensure values are non-negative floats, default to 0.0 otherwise
        download_mbps = max(0.0, float(download_mbps))
        upload_mbps = max(0.0, float(upload_mbps))
        ping_ms = max(0.0, float(ping_ms))

        return download_mbps, upload_mbps, ping_ms

    except (speedtest.SpeedtestException, requests.exceptions.RequestException, ConnectionError, TimeoutError) as e:
        # Catch specific expected network/provider errors first
        raise ConnectionError(f"Network/Provider error during {operation}: {e}") from e
    except Exception as e:
        # Log unexpected errors for better diagnostics
        tb_str = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        log_error(f"Unexpected error during {operation}: {e}\n{tb_str}")
        raise RuntimeError(f"Unexpected error during {operation}: {e}") from e

# --- End Core Check Functions ---


# --- Retry Logic (Unchanged from previous step) ---
def execute_with_retry(
    func: Callable[..., Any],
    args: tuple = (),
    kwargs: dict = {},
    operation_name: str = "Operation",
    max_retries: int = MAX_RETRIES,
    retry_delay: int = RETRY_DELAY
) -> Tuple[Any, str]:
    """Executes a function with retry logic."""
    last_exception = None
    initial_exception = None
    try:
        result = func(*args, **kwargs)
        return result, "Success"
    except Exception as e:
        print(f"    Error ({operation_name}): {e} - Retrying...")
        log_to_text(f"Error ({operation_name}): {e} - Retrying...")
        last_exception = e
        initial_exception = e # Store the first exception

    for i in range(max_retries):
        retry_num = i + 1
        print(f"      Retry {retry_num}/{max_retries} for {operation_name} in {retry_delay}s...")
        time.sleep(retry_delay)
        try:
            result = func(*args, **kwargs)
            print(f"    {operation_name}: Succeeded on retry {retry_num}.")
            log_to_text(f"Info ({operation_name}): Succeeded on retry {retry_num} after initial error: {initial_exception}") # Log success after retry
            return result, f"Success (Retry {retry_num})"
        except Exception as e:
            print(f"      Retry {retry_num} failed for {operation_name}: {e}")
            last_exception = e # Update last exception

    # If loop finishes, all retries failed
    if initial_exception and last_exception:
        log_persistent_error(operation_name, initial_exception, last_exception, max_retries)
    elif last_exception: # Should always have at least last_exception here
         log_persistent_error(operation_name, last_exception, last_exception, max_retries)
    else: # Should not happen, but defensively log if it does
         log_error(f"CRITICAL: Retry loop for {operation_name} finished without a recorded exception.")

    # Re-raise the last exception to signal failure to the caller
    if isinstance(last_exception, BaseException):
        raise last_exception
    else:
        # Fallback if last_exception isn't an exception for some reason
        raise RuntimeError(f"{operation_name} failed after {max_retries} retries, but last exception was not recorded properly.")


# --- Main Monitoring Function (Unchanged from previous step) ---
def monitor_internet():
    """Main worker function performing checks in a loop."""
    last_ip: Optional[str] = None
    current_ip_site_index: int = 0
    current_speed_provider_index: int = 0

    while True:
        cycle_start_dt = datetime.now()
        cycle_start_ts = get_timestamp()
        print(f"\n--- Starting Check Cycle: {cycle_start_ts} ---")
        current_cycle_start_time = time.monotonic()

        # --- Cycle Results Initialization ---
        ip_address: Optional[str] = None
        ip_city: Optional[str] = None
        ip_country: Optional[str] = None
        ip_provider: str = "N/A"
        ip_timestamp: str = cycle_start_ts # Default timestamp
        ip_status: str = "Not Run"

        download: float = 0.0
        upload: float = 0.0
        ping: float = 0.0
        speed_provider: str = "N/A"
        speed_timestamp: str = cycle_start_ts # Default timestamp
        speed_status: str = "Not Run"

        # --- 1. Perform IP Check ---
        ip_site = IP_CHECK_SITES[current_ip_site_index]
        ip_provider = ip_site['name']
        operation_ip = f"IP Check (via {ip_provider})"
        print(f"  Performing {operation_ip}...")
        try:
            # Expect a tuple (ip, city, country) from execute_with_retry
            (ip_address, ip_city, ip_country), ip_status = execute_with_retry(
                perform_ip_check, args=(ip_site,), operation_name=operation_ip
            )
            ip_timestamp = get_timestamp() # Get timestamp on success/successful retry

            if ip_status.startswith("Success"):
                 location_str = f" ({ip_city}, {ip_country})" if ip_city and ip_country else \
                                f" ({ip_city})" if ip_city else \
                                f" ({ip_country})" if ip_country else ""
                 if ip_address != last_ip:
                     change_msg = f"IP changed to: {ip_address}{location_str}" if last_ip else f"Current IP: {ip_address}{location_str}"
                     print(f"    {change_msg}")
                     last_ip = ip_address
                 else:
                     print(f"    IP unchanged ({ip_address}{location_str})")

                 # Add city/country to JSON log if available
                 json_log_entry = {
                     "timestamp": ip_timestamp, "type": "external_ip", "site": ip_provider,
                     "ip_address": ip_address, "status": ip_status
                 }
                 if ip_city: json_log_entry["city"] = ip_city
                 if ip_country: json_log_entry["country"] = ip_country
                 log_to_json(json_log_entry)

        except Exception as e:
            # This block catches failures *after* retries
            ip_timestamp = get_timestamp() # Timestamp of the final failure
            ip_status = "Failed"
            ip_address = None # Ensure IP is None on failure
            ip_city = None
            ip_country = None
            # Error is already logged by log_persistent_error in execute_with_retry
            print(f"    DEBUG: IP check ultimately failed after retries with {type(e).__name__}. See error logs.")


        # --- 2. Perform Speed Test ---
        speed_provider = SPEED_TEST_PROVIDERS[current_speed_provider_index]
        operation_speed = f"Speed Test (via {speed_provider})"
        print(f"  Performing {operation_speed}...")
        try:
            speed_results, speed_status = execute_with_retry(
                perform_speed_test, args=(speed_provider,), operation_name=operation_speed
            )
            speed_timestamp = get_timestamp() # Get timestamp on success/successful retry
            download, upload, ping = speed_results
            print(f"    Speed test results: Download: {download:.2f} Mbps, Upload: {upload:.2f} Mbps, Ping: {ping:.2f} ms")

            if speed_status.startswith("Success"):
                json_entry_speed = {
                    "timestamp": speed_timestamp, "type": "speed_test", "provider": speed_provider,
                    "ping_ms": round(ping, 2) if ping >= 0 else None, # Use >= 0 check
                    "download_mbps": round(download, 2) if download >= 0 else None,
                    "upload_mbps": round(upload, 2) if upload >= 0 else None,
                    "status": speed_status
                }
                # Add the IP address valid *at the time of the speed test* if available
                if ip_address: json_entry_speed["ip_address_at_test"] = ip_address
                log_to_json(json_entry_speed)

        except Exception as e:
            # This block catches failures *after* retries
            speed_timestamp = get_timestamp() # Timestamp of the final failure
            speed_status = "Failed"
            download, upload, ping = 0.0, 0.0, 0.0 # Reset results on failure
            # Error is already logged by log_persistent_error in execute_with_retry
            print(f"    DEBUG: Speed test ultimately failed after retries with {type(e).__name__}. See error logs.")


        # --- 3. Log Combined Result to Text File ---
        ip_str = ip_address if ip_address else "Failed"
        location_info = f" ({ip_city}, {ip_country})" if ip_city and ip_country else \
                        f" ({ip_city})" if ip_city else \
                        f" ({ip_country})" if ip_country else ""
        if ip_status == "Failed": location_info = "" # Don't show location if IP check failed

        # Use >= 0 check for speed results before formatting
        ping_str = f"{ping:.2f} ms" if ping >= 0 and speed_status != "Failed" else "N/A"
        down_str = f"{download:.2f} Mbps" if download >= 0 and speed_status != "Failed" else "N/A"
        up_str = f"{upload:.2f} Mbps" if upload >= 0 and speed_status != "Failed" else "N/A"
        if speed_status == "Failed":
             ping_str = down_str = up_str = "Failed"

        # Use individual timestamps for each check
        combined_log_message = (
             f"[{ip_timestamp}] IP Check ({ip_status} via {ip_provider}): IP: {ip_str}{location_info}\n"
             f"[{speed_timestamp}] Speed Test ({speed_status} via {speed_provider}): Ping: {ping_str}, Download: {down_str}, Upload: {up_str}"
        )
        log_to_text(combined_log_message)

        # --- 4. Prepare for NEXT Cycle ---
        # Cycle through IP providers
        current_ip_site_index = (current_ip_site_index + 1) % len(IP_CHECK_SITES)
        # Cycle through Speed Test providers
        current_speed_provider_index = (current_speed_provider_index + 1) % len(SPEED_TEST_PROVIDERS)

        # --- 5. Cycle End & Sleep ---
        cycle_duration = time.monotonic() - current_cycle_start_time
        sleep_time = CHECK_CYCLE_INTERVAL - cycle_duration
        if sleep_time > 0:
            # print(f"    Cycle finished in {cycle_duration:.2f}s. Sleeping for {sleep_time:.2f}s...") # Optional verbose logging
            time.sleep(sleep_time)
        else:
            warning_msg = f"Warning: Cycle duration ({cycle_duration:.2f}s) exceeded interval ({CHECK_CYCLE_INTERVAL}s). Starting next cycle immediately."
            print(warning_msg)
            log_to_text(warning_msg)
# --- End Main Monitoring Function ---


# --- Main Execution (Unchanged) ---
def initialize_logs():
    """Writes initial entries to log files."""
    ts = get_timestamp()
    print("Initializing log files...")
    log_to_text(f"--- Monitor starting: {ts} ---")
    log_error(f"--- Text error log initialized: {ts} ---")
    init_error_data = {
        "timestamp": ts, "operation": "System Initialization", "status": "JSON error log initialized.",
        "initial_error_type": None, "initial_error_message": None, "last_error_type": None,
        "last_error_message": None, "traceback": None
    }
    log_error_to_json(init_error_data)
    print(f"  Main Log (Text): {TEXT_LOG_FILE}")
    print(f"  Main Log (JSON): {JSON_LOG_FILE}")
    print(f"  Error Log (Text): {ERROR_LOG_FILE}")
    print(f"  Error Log (JSON): {ERROR_JSON_LOG_FILE}")

def main():
    """Main script execution function."""
    start_ts = get_timestamp()
    print(f"Script started at: {start_ts}")
    print(f"Log files will be stored in: {LOG_DIRECTORY}")

    initialize_logs()

    print("Starting internet monitor worker thread...")
    monitor_thread = threading.Thread(target=monitor_internet, daemon=True, name="InternetMonitorThread")
    monitor_thread.start()

    print(f"Speed test sequence: {' -> '.join(SPEED_TEST_PROVIDERS)} (repeats)")
    # Make sure the printed sequence reflects the actual expanded list
    print(f"IP check sequence: {' -> '.join([s['name'] for s in IP_CHECK_SITES])} (repeats)")
    print("Press Ctrl+C to stop.")

    try:
        while monitor_thread.is_alive():
            monitor_thread.join(timeout=1.0)
    except KeyboardInterrupt:
        stop_ts = get_timestamp()
        print(f"\n[{stop_ts}] Ctrl+C detected. Stopping monitor...")
        stop_msg = "Monitor stopped by user (Ctrl+C)"
        log_error(stop_msg)
        stop_error_data = {
            "timestamp": stop_ts, "operation": "System Shutdown", "status": stop_msg,
            "initial_error_type": "KeyboardInterrupt", "initial_error_message": stop_msg,
            "last_error_type": None, "last_error_message": None, "traceback": None
        }
        log_error_to_json(stop_error_data)
        log_to_text(f"--- {stop_msg} at {stop_ts} ---")
    except Exception as e:
        error_ts = get_timestamp()
        tb_str = traceback.format_exc()
        error_msg = f"FATAL: Unexpected error in main execution thread: {e}\n{tb_str}"
        print(error_msg, file=sys.stderr)
        try:
            log_to_text(f"--- {error_msg} ---")
            log_error(error_msg)
            main_thread_error_data = {
                 "timestamp": error_ts, "operation": "Main Thread Execution", "status": "Caught unhandled exception",
                 "initial_error_type": type(e).__name__, "initial_error_message": str(e),
                 "last_error_type": type(e).__name__, "last_error_message": str(e), "traceback": tb_str
            }
            log_error_to_json(main_thread_error_data)
        except Exception as log_e:
             print(f"\n[{get_timestamp()}] CRITICAL: Additionally failed to log the final main thread error: {log_e}", file=sys.stderr)
    finally:
        print(f"Monitor finished at: {get_timestamp()}.")

if __name__ == "__main__":
    main()
# --- End Main Execution ---