"""Loop detection for repetitive tool calls."""

import logging
from typing import List, Dict, Any, Optional

from .models import ToolCallRecord

logger = logging.getLogger(__name__)


class LoopDetector:
    """Detects repetitive tool call patterns."""
    
    def __init__(self, window_size: int = 6, threshold: float = 0.85, max_repetitions: int = 3):
        self.window_size = window_size
        self.threshold = threshold
        self.max_repetitions = max_repetitions
        self._history: List[List[Dict]] = []
        self._warning_count = 0
    
    def record(self, records: List[ToolCallRecord]):
        """Record tool calls."""
        snapshot = [
            {"tool": r.tool_name, "args": self._normalize(r.arguments)}
            for r in records
        ]
        self._history.append(snapshot)
        
        if len(self._history) > self.window_size * 2:
            self._history = self._history[-self.window_size * 2:]
    
    def detect(self) -> tuple[bool, Optional[str]]:
        """Detect if we're in a loop."""
        if len(self._history) < self.window_size:
            return False, None
        
        recent = self._history[-self.window_size:]
        repetitions = 0
        
        for i in range(len(self._history) - self.window_size):
            window = self._history[i:i + self.window_size]
            if self._similarity(window, recent) >= self.threshold:
                repetitions += 1
        
        if repetitions >= self.max_repetitions:
            self._warning_count += 1
            warning = self._get_warning()
            logger.warning(f"Loop detected! Repetitions={repetitions}")
            return True, warning
        
        if repetitions == 0:
            self._warning_count = 0
        
        return False, None
    
    def _similarity(self, a: List[List[Dict]], b: List[List[Dict]]) -> float:
        """Calculate similarity between two windows."""
        if len(a) != len(b):
            return 0.0
        
        matches = 0
        for calls_a, calls_b in zip(a, b):
            if calls_a == calls_b:
                matches += 1
        
        return matches / len(a)
    
    def _normalize(self, args: Dict) -> Dict:
        """Normalize arguments by removing volatile fields."""
        volatile = ('_at', '_time', '_id', 'timestamp')
        return {
            k: v for k, v in args.items()
            if not any(k.endswith(s) for s in volatile)
        }
    
    def _get_warning(self) -> str:
        base = """⚠️ **Loop Detection Warning**

The agent appears to be repeating similar actions without making progress.

Please try:
1. Summarize what you've learned so far
2. Take a different approach
3. Consider if the task is complete
4. Ask for guidance if stuck
"""
        if self._warning_count > 1:
            base += f"\n(This is warning #{self._warning_count})"
        return base
    
    def reset(self):
        """Clear detection history."""
        self._history.clear()
        self._warning_count = 0
