import re
from pathlib import Path

# Dangerous command patterns (regex for better matching)
BLOCKED_PATTERNS = [
    r'rm\s+-rf\s+/',           # rm -rf /
    r'rm\s+-rf\s+/\*',         # rm -rf /*
    r'del\s+/f\s+/q\s+[A-Z]:', # del /f /q C:
    r'format\s+[A-Z]:',        # format C:
    r'shutdown',               # Any shutdown
    r'rmdir\s+/s',            # rmdir /s
    r'dd\s+if=.*of=/dev/',    # dd to device
    r':\(\)\s*\{\s*:\|:&\s*\}\s*;\s*:',  # Fork bomb (escaped parens — must match!)
    r'mkfs\.',                # Format filesystem
]

# Allowed paths for file operations
ALLOWED_PATHS = [
    "C:\\Users",
    "C:\\Temp",
    "/tmp",
    "/home",
]

FORBIDDEN_PATHS = [
    "C:\\Windows\\System32",
    "C:\\Program Files",
    "/etc",
    "/bin",
    "/usr/bin",
]

class SafetyChecker:
    """Enhanced safety checker with path validation"""
    
    @staticmethod
    def is_command_safe(command: str) -> tuple[bool, str]:
        """
        Check if command is safe to execute
        
        Returns:
            (is_safe, reason)
        """
        # Check against blocked patterns
        for pattern in BLOCKED_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return False, f"Blocked: matches dangerous pattern '{pattern}'"
        
        # Check for suspicious keywords
        dangerous_keywords = ['virus', 'malware', 'hack', 'crack']
        for keyword in dangerous_keywords:
            if keyword in command.lower():
                return False, f"Blocked: contains suspicious keyword '{keyword}'"
        
        return True, "Command is safe"
    
    @staticmethod
    def is_path_safe(path: str) -> tuple[bool, str]:
        """
        Check if file path is safe to access
        
        Returns:
            (is_safe, reason)
        """
        path_obj = Path(path).resolve()
        path_str = str(path_obj)
        
        # Check forbidden paths
        for forbidden in FORBIDDEN_PATHS:
            if path_str.startswith(forbidden):
                return False, f"Blocked: path in forbidden directory '{forbidden}'"
        
        # Check if in allowed paths
        for allowed in ALLOWED_PATHS:
            if path_str.startswith(allowed):
                return True, "Path is in allowed directory"
        
        # Not in allowed paths - require explicit permission
        return False, f"Blocked: path '{path}' not in allowed directories"

# Backward compatibility
def is_safe(command: str) -> bool:
    """Legacy function for compatibility"""
    safe, _ = SafetyChecker.is_command_safe(command)
    return safe