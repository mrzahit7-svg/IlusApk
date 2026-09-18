import re
import random
import ast
import operator
import threading
import time
import os
try:
    import requests
except ImportError:
    requests = None
import sys
import importlib
import types
import gc
import subprocess
import json
import queue

# When this file is launched directly (`python ilus_pyside.py`), Python
# registers it in sys.modules under the name "__main__". If a user's Python
# library later does `import ilus_pyside` (to reach run_on_gui_thread), Python
# would NOT find it under that name and would re-import + re-execute this
# entire file as a brand-new, SEPARATE module object - which would have its
# own separate _GUI_BRIDGE (never set, since main() only runs once) and
# duplicate class definitions. This line makes sure "ilus_pyside" always
# points at the one already-running module, whatever name Python gave it,
# so library imports safely reuse it instead of re-executing the file.
sys.modules.setdefault("ilus_pyside", sys.modules[__name__])

# The interpreter should not impose any artificial limit on how large a number
# users can compute/print with write(). Python 3.11+ adds a default 4300-digit
# cap on int<->str conversion (a DoS guard for untrusted input); disable it here
# so big-number arithmetic in Ilus scripts never hits it.
if hasattr(sys, "set_int_max_str_digits"):
    sys.set_int_max_str_digits(0)

# ============================================================================
# LIBRARY MANAGEMENT SYSTEM
# ============================================================================

class LibraryManager:
    """Manages external and custom libraries"""
    
    def __init__(self):
        self.loaded_libraries = {}
        self.custom_modules = {}
        self.builtin_libs = {'math', 'random', 'os', 'time', 'sys'}
    
    def import_library(self, lib_name):
        """Import a library with error handling"""
        if lib_name in self.loaded_libraries:
            return self.loaded_libraries[lib_name], None
        
        if lib_name in self.builtin_libs:
            try:
                lib = __import__(lib_name)
                self.loaded_libraries[lib_name] = lib
                return lib, None
            except Exception as e:
                return None, f"Error: Cannot import built-in library '{lib_name}': {str(e)}"
        
        try:
            lib = importlib.import_module(lib_name)
            self.loaded_libraries[lib_name] = lib
            return lib, None
        except ImportError:
            return None, f"Error: Library '{lib_name}' not found"
        except Exception as e:
            return None, f"Error: Failed to import '{lib_name}': {str(e)}"
    
    def register_custom_module(self, module_name, module_object):
        """Register a custom module"""
        self.custom_modules[module_name] = module_object
    
    def get_module_functions(self, module_name):
        """Get all functions from a module"""
        if module_name in self.custom_modules:
            return self.custom_modules[module_name]
        if module_name in self.loaded_libraries:
            return self.loaded_libraries[module_name]
        return None

# ============================================================================
# GUI COMPONENT SYSTEM
# ============================================================================

class GUIComponent:
    """Base class for GUI components"""
    
    def __init__(self, component_type, properties=None):
        self.type = component_type
        self.properties = properties or {}
        self.validate()
    
    def validate(self):
        """Validate component properties"""
        required_props = {
            'Button': ['text', 'size'],
            'Text': ['content', 'size'],
            'Input': ['placeholder', 'size'],
            'Label': ['text', 'size'],
            'Image': ['path', 'size'],
        }
        
        if self.type in required_props:
            for prop in required_props[self.type]:
                if prop not in self.properties:
                    self.properties[prop] = ""
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            'type': self.type,
            'properties': self.properties
        }

class GUIBuilder:
    """Build GUI components"""
    
    @staticmethod
    def create_button(text, size, bind_func=None):
        """Create a button component"""
        props = {
            'text': str(text),
            'size': size,
            'bind': bind_func,
            'type': 'block'
        }
        return GUIComponent('Button', props)
    
    @staticmethod
    def create_text(content, size, color='black'):
        """Create a text component"""
        props = {
            'content': str(content),
            'size': size,
            'color': color,
            'type': 'UI'
        }
        return GUIComponent('Text', props)
    
    @staticmethod
    def create_input(placeholder, size):
        """Create an input component"""
        props = {
            'placeholder': str(placeholder),
            'size': size,
            'type': 'UI'
        }
        return GUIComponent('Input', props)
    
    @staticmethod
    def create_label(text, size, color='black'):
        """Create a label component"""
        props = {
            'text': str(text),
            'size': size,
            'color': color
        }
        return GUIComponent('Label', props)

# ============================================================================
# STATISTICS & VALIDATION SYSTEM
# ============================================================================

class PropertyValidator:
    """Validate game object properties"""
    
    VALID_PROPERTIES = {
        'anchored': bool,
        'transparency': float,
        'collision': bool,
        'color': str,
        'size': tuple,
        'x': int,
        'y': int,
        'text': str,
        'text_color': str,
        'text_size': tuple,
        'type': str,
        'can_collide': bool,
        'can_jump': bool,
        'is_player': bool,
        'gui_bound_event': str,
    }
    
    @staticmethod
    def validate_property(prop_name, value):
        """Validate a single property"""
        if prop_name not in PropertyValidator.VALID_PROPERTIES:
            return False, f"Error: Unknown property '{prop_name}'"
        
        expected_type = PropertyValidator.VALID_PROPERTIES[prop_name]
        
        if prop_name == 'anchored':
            if not isinstance(value, bool):
                return False, f"Error: 'Anchored' must be boolean, got {type(value).__name__}"
            return True, None
        
        if prop_name == 'transparency':
            if not isinstance(value, (int, float)):
                return False, f"Error: 'Transparency' must be float, got {type(value).__name__}"
            if not (0.0 <= value <= 1.0):
                return False, "Error: 'Transparency' must be between 0.0 and 1.0"
            return True, None
        
        if prop_name == 'collision':
            if not isinstance(value, bool):
                return False, f"Error: 'Collision' must be boolean, got {type(value).__name__}"
            return True, None
        
        if not isinstance(value, expected_type):
            return False, f"Error: '{prop_name}' must be {expected_type.__name__}, got {type(value).__name__}"
        
        return True, None
    
    @staticmethod
    def validate_object(obj_dict):
        """Validate all properties of an object"""
        errors = []
        
        for prop, value in obj_dict.items():
            is_valid, error = PropertyValidator.validate_property(prop, value)
            if not is_valid:
                errors.append(error)
        
        return len(errors) == 0, errors

class GameStatistics:
    """Track game statistics"""
    
    def __init__(self):
        self.objects_created = 0
        self.objects_with_collision = 0
        self.objects_with_transparency = 0
        self.anchored_objects = 0
        self.collision_count = 0
        self.stats = {}
    
    def record_object_creation(self, obj_type, properties):
        """Record object creation"""
        self.objects_created += 1
        
        if properties.get('collision', False):
            self.objects_with_collision += 1
        
        if properties.get('transparency', 0.0) > 0.0:
            self.objects_with_transparency += 1
        
        if properties.get('anchored', False):
            self.anchored_objects += 1
        
        if obj_type not in self.stats:
            self.stats[obj_type] = {'count': 0, 'with_collision': 0, 'transparent': 0, 'anchored': 0}
        
        self.stats[obj_type]['count'] += 1
        if properties.get('collision'):
            self.stats[obj_type]['with_collision'] += 1
        if properties.get('transparency', 0.0) > 0.0:
            self.stats[obj_type]['transparent'] += 1
        if properties.get('anchored'):
            self.stats[obj_type]['anchored'] += 1
    
    def record_collision(self):
        """Record a collision event"""
        self.collision_count += 1
    
    def get_report(self):
        """Get statistics report"""
        report = f"""
GAME STATISTICS REPORT
======================
Total Objects Created: {self.objects_created}
Objects with Collision: {self.objects_with_collision}
Objects with Transparency: {self.objects_with_transparency}
Anchored Objects: {self.anchored_objects}
Total Collisions Detected: {self.collision_count}

OBJECT TYPE BREAKDOWN:
"""
        for obj_type, counts in self.stats.items():
            report += f"  {obj_type}: {counts['count']} (collision: {counts['with_collision']}, transparent: {counts['transparent']}, anchored: {counts['anchored']})\n"
        
        return report

# ============================================================================
# VALID COLORS LIST
# ============================================================================

VALID_COLORS = {
    "red", "green", "blue", "black", "white", "yellow", "purple", 
    "orange", "pink", "brown", "gray", "cyan", "magenta"
}

# ============================================================================
# ILUS KEYWORDS
# ============================================================================

ILUS_KEYWORDS = {
    "True", "False", "None", "Repeat", "table", "pass", "function", 
    "continue", "break", "in", "return", "Target", "Choose", "Fail", 
    "and", "or", "is", "work", "working", "class", "open", "zip", 
    "think", "locals", "globals", "del", "Score", "import", "TYPE", 
    "wait", "weld", "GUI", "inherits", "extends", "parent", "Anchored",
    "Collision", "Create"
}


class IlusConvertedList(list):
    """A list that resulted from a type conversion (str()/int()/fround()/bool() applied
    to a list), rather than a native list literal. Elements are now individually-typed
    values, not 'list' items, so they print one-per-line instead of comma-joined."""
    pass


class IlusConvertedTuple(tuple):
    """Tuple counterpart of IlusConvertedList: the result of int()/fround()/bool()
    applied to a tuple, where each element was individually converted. Prints
    one-per-line like a converted list, instead of the '(a,b,c)' literal format."""
    pass


# --- # --- LEXER/PARSER/WRITER ENGINE ---
class IlusSelection(list):
    """A list of values picked out via table.list[a,b] (range) or table.list[a=b=c] (multi-index)
    selection syntax. Behaves exactly like a list everywhere (isinstance(x, list) is True), but
    write() displays it space-separated instead of comma-separated."""
    pass


class SafeMathEvaluator:
    operators = {
        ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
        ast.Pow: operator.pow, ast.BitXor: operator.xor, ast.USub: operator.neg, ast.UAdd: operator.pos,
    }

    def __init__(self, variables, interpreter=None):
        self.variables = variables
        self.interpreter = interpreter
        self.error = None

    def get_ilus_type(self, value):
        if value is None: return "none"
        if isinstance(value, bool): return "bool" 
        elif isinstance(value, int): return "int"
        elif isinstance(value, float): return "fround"
        elif isinstance(value, str): return "str"
        elif isinstance(value, list): return "list"
        elif isinstance(value, tuple): return "tuple"
        elif isinstance(value, dict):
            if "__type__" in value:
                if value["__type__"] in ["instance", "game_score"]: return value["__type__"]
                if value["__type__"] == "file": return "file"
            if "type" in value and value.get("type") in ["block", "circle", "UI", "area", "score"]:
                return "game_object"
            return "dict"
        return "unknown"

    def check_type_compatibility(self, op_type, left, right):
        t1 = self.get_ilus_type(left)
        t2 = self.get_ilus_type(right)
        
        if (t1 == "int" and t2 == "none") or (t1 == "none" and t2 == "int"):
            self.error = "Logic error: Cannot perform operations between 'int' and 'None' types! 💀"
            return False
            
        if (t1 == "bool" and t2 == "none") or (t1 == "none" and t2 == "bool"):
            if op_type == ast.Add: return True
            self.error = "Logic error: only addition (+) can be performed between 'bool' and 'None'."
            return False

        if op_type == ast.BitXor:
            if t1 in ("int", "bool") and t2 in ("int", "bool"): return True
            self.error = "Logic error: XOR (^) operation can only be performed between 'int' and 'bool' types."
            return False
            
        if t1 == "int" and t2 == "int": return True
        elif t1 == "int" and t2 == "fround": return True
        elif t1 == "fround" and t2 == "int": return True
        elif t1 == "fround" and t2 == "fround": return True
        
        # allowance for list/tuple arithmetic (Element-wise operations)
        elif (t1 == "list" and t2 in ("int", "fround")) or (t1 in ("int", "fround") and t2 == "list"):
            return True
        elif (t1 == "tuple" and t2 in ("int", "fround")) or (t1 in ("int", "fround") and t2 == "tuple"):
            return True
            
        elif t1 == "str" and t2 == "str":
            if op_type == ast.Add: return True
            self.error = "Logic error: only addition (+) can be performed between texts."
            return False
        elif t1 == "list" and t2 == "list":
            if op_type == ast.Add: return True
            self.error = "Logic error: only addition (+) can be performed between lists."
            return False
        elif t1 == "tuple" and t2 == "tuple":
            if op_type == ast.Add: return True
            self.error = "Logic error: only addition (+) can be performed between tuples."
            return False
        elif t1 == "bool" and t2 == "bool": return True
        else:
            self.error = f"Logic error: operation forbidden between types '{t1}' and '{t2}'."
            return False

    def evaluate(self, expr):
        if not expr or not expr.strip():
            self.error = "Statistics error: mathematical expression cannot be empty."
            return None
            
        if self.interpreter:
            while 'work(working(' in expr:
                match = re.search(r'work\(working\("([^"]+)"\)\)', expr)
                if not match: break
                val, err = self.interpreter.execute_game_working_logic(match.group(1))
                if err or val is None: break
                if isinstance(val, tuple):
                    expr = expr.replace(match.group(0), str(val))
                else:
                    expr = expr.replace(match.group(0), str(val))

        expr_ast = expr
        expr_ast = re.sub(r'(?<![\w\.\)\*])\*([a-zA-Z_]\w*)', r'__STAR__\1', expr_ast)

        if not re.fullmatch(r'[\w\s\+\-\*\/\%\(\)\.\^\"\',\[\]\{\}\:\!\?\@\#\$\&\=\<\>\|]+', expr_ast):
            self.error = f"Statistics error: Invalid characters in mathematical expression: {expr}"
            return None
        try:
            tree = ast.parse(expr_ast, mode='eval').body
        except Exception:
            self.error = f"Statistics error: Mathematical expression format is invalid: {expr}"
            return None
        result = self._eval_node(tree)
        if self.error: return None
        return result

    def _eval_node(self, node):
        if self.error: return None
        if isinstance(node, ast.Constant): return node.value
        elif isinstance(node, ast.Num): return node.n
        elif isinstance(node, ast.Str): return node.s
        elif isinstance(node, ast.List): return [self._eval_node(elt) for elt in node.elts]
        elif isinstance(node, ast.Tuple): return tuple(self._eval_node(elt) for elt in node.elts)
        elif isinstance(node, ast.Dict):
            return {self._eval_node(k): self._eval_node(v) for k, v in zip(node.keys, node.values)}
        elif isinstance(node, ast.Attribute):
            obj = self._eval_node(node.value)
            if self.error: return None
            if isinstance(obj, dict) and obj.get("__type__") in ["instance", "game_score"]:
                if node.attr in obj.get("attrs", {}):
                    return obj["attrs"][node.attr]
                self.error = f"Logic error: '{node.attr}' property not found."
                return None
            self.error = "Logic error: this object cannot read an attribute."
            return None
        elif isinstance(node, ast.Subscript):
            value = self._eval_node(node.value)
            slice_val = self._eval_node(node.slice)
            if self.error: return None
            
            if isinstance(value, dict) and value.get("__type__") == "instance":
                cls_name = value["__class__"]
                self.error = f"Logic error: class '{cls_name}' does not support indexing."
                return None
            
            if isinstance(value, (list, tuple, str)):
                if isinstance(slice_val, int):
                    if 0 <= slice_val < len(value) or -len(value) <= slice_val < 0:
                        return value[slice_val]
                    else:
                        self.error = f"Logic error: Index does not exist: {slice_val}"
                        return None
                else:
                    self.error = f"Logic error: Index must be an integer."
                    return None
            elif isinstance(value, dict):
                if slice_val in value: return value[slice_val]
                else:
                    self.error = f"Logic error: Key not found: {slice_val}"
                    return None
            else:
                self.error = f"Logic error: Cannot index this type."
                return None

        elif isinstance(node, ast.BoolOp):
            values = [self._eval_node(v) for v in node.values]
            if isinstance(node.op, ast.And):
                if all(isinstance(v, str) for v in values): return " ".join(values)
                return " ".join(str(v) for v in values)
            elif isinstance(node.op, ast.Or): return values[-1]
        elif isinstance(node, ast.BinOp):
            left = self._eval_node(node.left)
            right = self._eval_node(node.right)
            if self.error: return None
            op_type = type(node.op)
            if op_type not in self.operators:
                self.error = "Logic error: unsupported mathematical operator."
                return None

            if self.interpreter is not None:
                for side_node, side_val in ((node.left, left), (node.right, right)):
                    if isinstance(side_node, ast.Name) and isinstance(side_val, list):
                        if getattr(self.interpreter, "variable_origins", {}).get(side_node.id) == "int":
                            self.error = f"Logic error: '{side_node.id}' is officially typed as int and cannot be used in list-style arithmetic."
                            return None

            if not self.check_type_compatibility(op_type, left, right): return None
            if op_type in (ast.Div, ast.FloorDiv, ast.Mod) and right == 0:
                self.error = "Logic error: cannot divide by zero."
                return None
            if left is None or right is None: return left if left is not None else right
            
            # arithmetic operation logic on list/tuple elements (List/Tuple Math) - only for
            # lists/tuples explicitly converted to int()/fround() first; a raw (unconverted)
            # list or tuple must not support this.
            if isinstance(left, (list, tuple)) and isinstance(right, (int, float)):
                if not isinstance(left, (IlusConvertedList, IlusConvertedTuple)):
                    self.error = "Logic error: this operator is not supported on lists/tuples. Convert it to int() or fround() first."
                    return None
                wrapper = IlusConvertedList if isinstance(left, IlusConvertedList) else IlusConvertedTuple
                try:
                    result = [self.operators[op_type](x, right) for x in left]
                    return wrapper(result)
                except Exception as e:
                    self.error = f"Logic error: error while operating on list/tuple elements: {e}"
                    return None
            elif isinstance(right, (list, tuple)) and isinstance(left, (int, float)):
                if not isinstance(right, (IlusConvertedList, IlusConvertedTuple)):
                    self.error = "Logic error: this operator is not supported on lists/tuples. Convert it to int() or fround() first."
                    return None
                wrapper = IlusConvertedList if isinstance(right, IlusConvertedList) else IlusConvertedTuple
                try:
                    result = [self.operators[op_type](left, x) for x in right]
                    return wrapper(result)
                except Exception as e:
                    self.error = f"Logic error: error while operating on list/tuple elements: {e}"
                    return None
                    
            return self.operators[op_type](left, right)
        elif isinstance(node, ast.UnaryOp):
            operand = self._eval_node(node.operand)
            if operand is None: return None
            op_type = type(node.op)
            if op_type not in self.operators:
                self.error = "Logic error: unsupported unary operator."
                return None
            return self.operators[op_type](operand)
        elif isinstance(node, ast.Compare):
            if all(isinstance(op, ast.Is) for op in node.ops):
                left_val = self._eval_node(node.left)
                vals = [left_val]
                for comp in node.comparators: vals.append(self._eval_node(comp))
                types = [self.get_ilus_type(v) for v in vals]
                return len(set(types)) == 1
            if len(node.ops) != 1 or len(node.comparators) != 1:
                self.error = "Logic error: comparison can only be performed between two values."
                return None
            op_type = type(node.ops[0])
            
            if op_type == ast.In:
                right = self._eval_node(node.comparators[0])
                left = self._eval_node(node.left)
                if self.error: return None
                
                if not isinstance(right, (list, tuple, str, dict)):
                    self.error = "Logic error: the 'in' operator is only valid for iterable types."
                    return None
                return left in right
                
            left = self._eval_node(node.left)
            right = self._eval_node(node.comparators[0])
            if left is None or right is None: return None
            
            if op_type == ast.Is:
                t1 = self.get_ilus_type(left)
                t2 = self.get_ilus_type(right)
                return t1 == t2
            elif op_type == ast.Eq:
                if isinstance(right, dict) and "__ilus_type_marker__" in right:
                    return isinstance(left, dict) and left.get("type") == right["__ilus_type_marker__"]
                if isinstance(left, dict) and "__ilus_type_marker__" in left:
                    return isinstance(right, dict) and right.get("type") == left["__ilus_type_marker__"]
                return left == right
            elif op_type == ast.NotEq:
                if isinstance(right, dict) and "__ilus_type_marker__" in right:
                    return not (isinstance(left, dict) and left.get("type") == right["__ilus_type_marker__"])
                if isinstance(left, dict) and "__ilus_type_marker__" in left:
                    return not (isinstance(right, dict) and right.get("type") == left["__ilus_type_marker__"])
                return left != right
            elif op_type == ast.Lt: 
                t1, t2 = self.get_ilus_type(left), self.get_ilus_type(right)
                if t1 in ("int", "fround") and t2 in ("int", "fround"): return left < right
                self.error = "Logic error: types are not suitable for the < operator."
                return None
            elif op_type == ast.LtE:
                t1, t2 = self.get_ilus_type(left), self.get_ilus_type(right)
                if t1 in ("int", "fround") and t2 in ("int", "fround"): return left <= right
                self.error = "Logic error: types are not suitable for the <= operator."
                return None
            elif op_type == ast.Gt:
                t1, t2 = self.get_ilus_type(left), self.get_ilus_type(right)
                if t1 in ("int", "fround") and t2 in ("int", "fround"): return left > right
                self.error = "Logic error: types are not suitable for the > operator."
                return None
            elif op_type == ast.GtE:
                t1, t2 = self.get_ilus_type(left), self.get_ilus_type(right)
                if t1 in ("int", "fround") and t2 in ("int", "fround"): return left >= right
                self.error = "Logic error: types are not suitable for the >= operator."
                return None
            else:
                self.error = "Logic error: unknown comparison operator in math engine."
                return None
        elif isinstance(node, ast.Name):
            node_id = node.id
            if node_id.startswith('__STAR__'):
                node_id = '*' + node_id[8:]
                
            if node_id in ("True", "False", "None"):
                if node_id == "True": return True
                if node_id == "False": return False
                return None
            if node_id in ("true", "false", "none"):
                if node_id in self.variables: return self.variables[node_id]
                self.error = f"Statistics error: '{node_id}' must be counted as bool function! Correct usage: {node_id.capitalize()}"
                return None
            if node_id not in self.variables:
                self.error = f"Logic error: Undefined variable used: '{node_id}'"
                return None
            return self.variables[node_id]
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "abs":
                if len(node.args) != 1:
                    self.error = "Logic error: abs() function only accepts 1 argument."
                    return None
                arg_val = self._eval_node(node.args[0])
                if isinstance(arg_val, (int, float)) and not isinstance(arg_val, bool):
                    return abs(arg_val)
                self.error = "Logic error: abs() function only valid for 'int' or 'fround' types."
                return None
            elif isinstance(node.func, ast.Name) and node.func.id == "round":
                if len(node.args) != 1:
                    self.error = "Logic error: round() function only accepts 1 argument."
                    return None
                arg_val = self._eval_node(node.args[0])
                if isinstance(arg_val, (int, float)) and not isinstance(arg_val, bool):
                    return int(arg_val + 0.5) if arg_val >= 0 else int(arg_val - 0.5)
                self.error = "Logic error: round() function only valid for 'int' or 'fround' types."
                return None
            elif isinstance(node.func, ast.Name) and node.func.id == "TYPE":
                if len(node.args) != 1:
                    self.error = "Logic error: TYPE() function only accepts 1 argument."
                    return None
                arg_val = self._eval_node(node.args[0])
                return {"__ilus_type_marker__": str(arg_val)}
            elif isinstance(node.func, ast.Name) and node.func.id in ("int", "str", "fround", "bool"):
                if len(node.args) != 1:
                    self.error = f"Logic error: {node.func.id}() function only accepts 1 argument."
                    return None
                arg_val = self._eval_node(node.args[0])
                if self.error: return None
                if self.interpreter is None:
                    self.error = f"Logic error: {node.func.id}() is not supported inside the math engine here."
                    return None
                res, err = self.interpreter.cast_value(node.func.id, arg_val)
                if err:
                    self.error = err
                    return None
                return res
            elif isinstance(node.func, ast.Name) and node.func.id == "zip":
                if len(node.args) == 1:
                    a1 = self._eval_node(node.args[0])
                    if isinstance(a1, dict) and a1.get("__type__") not in ["instance", "file", "game_score"]:
                        return ", ".join([f"{k}:{v}" for k, v in a1.items()])
                    self.error = "Logic error: single-argument zip() only accepts a dict."
                    return None
                elif len(node.args) == 2:
                    a1 = self._eval_node(node.args[0])
                    a2 = self._eval_node(node.args[1])
                    if isinstance(a1, (list, tuple)) and isinstance(a2, (list, tuple)):
                        min_len = min(len(a1), len(a2))
                        return [(a1[i], a2[i]) for i in range(min_len)]
                    t1, t2 = type(a1), type(a2)
                    if t1 == t2 and isinstance(a1, (int, float, str)):
                        return a1 + a2
                    self.error = "Logic error: incompatible types for zip()."
                    return None
                self.error = "Logic error: zip() requires 1 or 2 arguments."
                return None
            elif isinstance(node.func, ast.Name) and node.func.id == "len":
                if len(node.args) != 1:
                    self.error = "Error: len() requires exactly 1 argument."
                    return None
                arg_val = self._eval_node(node.args[0])
                if self.error: return None
                if isinstance(arg_val, bool):
                    self.error = "Error: len() must not be used with boolean (True/False) types!"
                    return None
                elif isinstance(arg_val, (int, float)):
                    str_val = str(arg_val).replace('.', '').lstrip('-')
                    return len(str_val)
                elif isinstance(arg_val, (str, list, tuple, dict)):
                    return len(arg_val)
                self.error = "Error: len() used for an unsupported type."
                return None
            if self.interpreter is not None:
                if isinstance(node.func, ast.Name):
                    fn_name = node.func.id
                    if fn_name in self.interpreter.functions:
                        arg_vals = [self._eval_node(a) for a in node.args]
                        if self.error: return None
                        res, err = self.interpreter.execute_function_with_values(fn_name, arg_vals)
                        if err:
                            self.error = err
                            return None
                        return res
                elif isinstance(node.func, ast.Attribute):
                    obj_val = self._eval_node(node.func.value)
                    if self.error: return None
                    if isinstance(obj_val, dict) and obj_val.get("__type__") == "instance":
                        method_name = node.func.attr
                        arg_vals = [self._eval_node(a) for a in node.args]
                        if self.error: return None
                        res, err = self.interpreter.execute_method_with_values(obj_val, obj_val["__class__"], method_name, arg_vals)
                        if err:
                            self.error = err
                            return None
                        return res
            self.error = "Logic error: this function call is not supported inside the math engine."
            return None
        else:
            self.error = "Logic error: structure not permitted by the system!"
            return None

# --- INTERPRETER ---
class IlusInterpreter:
    
    installed_packages = {}

    def __init__(self, request_input_callback=None):
        self.variables = {}
        self.variable_origins = {}  
        self.functions = {}  
        self.classes = {}  
        self.open_files = [] 
        self.logs = []
        self.game_slot_active = False
        self.should_end_game = False
        self.supported_devices = {"M": True, "P": True}
        self.lines_per_second = 0
        self._lines_executed_total = 0
        self._run_start_time = time.time()
        self.game_objects = []
        self.current_obj = None
        self.pc_controls = []
        self.pc_control_binds = {}
        self.should_stop = False
        self.request_input_callback = request_input_callback
        self.ilusaztar_imported = False
        self.live_input_state = {"left": False, "right": False, "up": False, "down": False, "jump": False}
        self.pending_input_value = None
        self.named_objects = {}
        self.imported_modules = set()
        self.gui_clicked_state = {}
        self.active_input_object = None  # the Input/InputBox object whose Bind handler is currently running; free(prompt) attaches its prompt text here for display
        self._scope_stack = []  # each entry: the set of variable names that existed BEFORE the current function call (i.e. the outer/global scope at that point)

    def log(self, message):
        self.logs.append(str(message))

    def ilus_type_name(self, value):
        # Bool must always be checked before int (in Python, bool is a subclass of int)
        if value is None: return "none"
        if isinstance(value, bool): return "bool"
        if isinstance(value, int): return "int"
        if isinstance(value, float): return "fround"
        if isinstance(value, str): return "str"
        if isinstance(value, list): return "list"
        if isinstance(value, tuple): return "tuple"
        if isinstance(value, dict):
            if value.get("__type__") == "file": return "file"
            if value.get("__type__") in ["instance", "game_score"]: return value.get("__class__", value["__type__"])
            return "dict"
        return "unknown"

    def unpack_for_iteration(self, value):
        """
        New feature: general 'unpack' logic for 'e in X' or 'Repeat: (e in X)'.
        Works for list/tuple/dict/str/int/fround/bool, not supported for file type.
        Elements inside a Tuple (including nested tuples) are fully unwound (flattened).
        """
        def flatten(item):
            if isinstance(item, tuple):
                out = []
                for sub in item:
                    out.extend(flatten(sub))
                return out
            return [item]

        if isinstance(value, dict) and value.get("__type__") == "file":
            return None, "Logic error: file type cannot be unpacked with 'in'."
        if isinstance(value, dict) and value.get("__type__") in ("instance", "game_score"):
            return None, "Logic error: This object cannot be unpacked with 'in'."

        if isinstance(value, (list, tuple)):
            items = []
            for it in value:
                items.extend(flatten(it))
            return items, None

        if isinstance(value, dict):
            items = []
            for k, v in value.items():
                items.extend(flatten(k))
                items.extend(flatten(v))
            return items, None

        if isinstance(value, str):
            return list(value), None

        if isinstance(value, (int, float, bool)):
            return [value], None

        return None, "Logic error: This type cannot be unpacked with 'in'."

    def safe_split_all_assignments(self, line):
        parts = []
        in_quotes = False
        paren_depth, bracket_depth, brace_depth = 0, 0, 0
        last_idx = 0
        i = 0
        while i < len(line):
            char = line[i]
            if char == '"': in_quotes = not in_quotes
            elif not in_quotes:
                if char == '(': paren_depth += 1
                elif char == ')': paren_depth -= 1
                elif char == '[': bracket_depth += 1
                elif char == ']': bracket_depth -= 1
                elif char == '{': brace_depth += 1
                elif char == '}': brace_depth -= 1
                elif char == '=':
                    if paren_depth == 0 and bracket_depth == 0 and brace_depth == 0:
                        prev = line[i-1] if i > 0 else ''
                        nxt = line[i+1] if i < len(line)-1 else ''
                        if prev not in '!<>' and nxt != '=':
                            parts.append(line[last_idx:i].strip())
                            last_idx = i + 1
            i += 1
        parts.append(line[last_idx:].strip())
        return parts

    def handle_write(self, line):
        match = re.match(r'^write\((.*)\)$', line)
        if not match: return None, "Logic error: write() format is wrong."
        arg_str = match.group(1).strip()
        
        in_match = re.match(r'^([\w,\s\*]+)\s+in\s+(.*)$', arg_str)
        if in_match:
            left_str = in_match.group(1).strip()
            right_str = in_match.group(2).strip()
            is_starred = right_str.startswith("*") and not right_str.startswith("**")
            resolve_str = right_str[1:].strip() if is_starred else right_str
            right_val, err = self.resolve_general_value(resolve_str, allow_raw_string=True)
            if err: return None, err
            extracted, err = self.unpack_for_iteration(right_val)
            if err: return None, err
            if is_starred:
                # write(e in *args) / write(f,b in *args): named vars pick elements
                # positionally from the unpacked *args values, space-joined on one line.
                var_names = [v.strip() for v in left_str.split(",") if v.strip()]
                n = len(var_names) if var_names else len(extracted)
                selected = extracted[:n]
                self.log(" ".join(map(str, selected)))
            else:
                self.log("\n".join(map(str, extracted)))
            return None, None
        
        args, unbal = self.smart_split(arg_str)
        if unbal:
            depth = 0
            for ch in arg_str:
                if ch in '([{': depth += 1
                elif ch in ')]}': depth -= 1
            if depth == 0 and arg_str.count(',') == 0 and arg_str.startswith('"') and arg_str.endswith('"'):
                args, unbal = [arg_str], False
        if unbal: return None, "Statistics error: parentheses inside write() are unbalanced or the value is incomplete."
        
        output_parts = []
        for arg in args:
            starred = arg.startswith("*") and not arg.startswith("**")
            resolve_arg = arg[1:].strip() if starred else arg
            val, err = self.resolve_general_value(resolve_arg, allow_raw_string=True)
            if err: return None, err
            
            if isinstance(val, dict) and "__type__" in val:
                if val["__type__"] == "instance":
                    cls_name = val["__class__"]
                    if "__walk__" in self.classes[cls_name]["methods"]:
                        walk_res, m_err = self.execute_method_with_values(val, cls_name, "__walk__", [])
                        if m_err: return None, m_err
                        if isinstance(walk_res, (list, tuple)):
                            output_parts.append("\n".join(map(str, walk_res)))
                        else:
                            output_parts.append(str(walk_res))
                    elif "__str__" in self.classes[cls_name]["methods"]:
                        res, m_err = self.execute_method_with_values(val, cls_name, "__str__", [])
                        if not m_err: output_parts.append(str(res))
                        else: output_parts.append(f"<instance of {val['__class__']}>")
                    else:
                        output_parts.append(f"<instance of {val['__class__']}>")
                elif val["__type__"] == "file": output_parts.append(f"<file '{val['name']}'>")
                elif val["__type__"] == "game_score": output_parts.append(f"<game_score value={val['attrs'].get('Value', 0)}>")
                else: output_parts.append(str(val))
            elif isinstance(val, list):
                if isinstance(val, IlusConvertedList):
                    output_parts.append("\n".join(map(str, val)))
                elif starred:
                    output_parts.append(",".join(map(str, val)))
                else:
                    output_parts.append("[" + ",".join(map(str, val)) + "]")
            elif isinstance(val, tuple):
                if isinstance(val, IlusConvertedTuple):
                    output_parts.append("\n".join(map(str, val)))
                else:
                    output_parts.append("(" + ",".join(map(str, val)) + ")")
            elif isinstance(val, dict) and "__ilus_type_marker__" in val:
                output_parts.append(f"<TYPE:{val['__ilus_type_marker__']}>")
            else: output_parts.append(str(val))
                
        if output_parts:
            self.log("\n".join(output_parts) if any("\n" in p for p in output_parts) else " ".join(output_parts))
        return None, None

    def handle_work_syntax(self, line):
        match = re.match(r'^work\((.*)\)$', line)
        if not match: return None, "Logic error: work format is wrong."
        inner = match.group(1).strip()
        if inner.startswith('working(') and inner.endswith(')'):
            work_arg = inner[8:-1].strip().strip('"')
            val, err = self.execute_game_working_logic(work_arg)
            return val, err
        return None, "Logic error: Unknown work command."

    def handle_import(self, mod_name, line_num):
        if mod_name == "ilusaztar":
            if mod_name not in IlusInterpreter.installed_packages:
                return f"Statistics error: package '{mod_name}' is not installed! Please install it first from the Terminal (ipm install {mod_name})."
            self.ilusaztar_imported = True
            self.log("[System]: 'ilusaztar' game module imported successfully.")
            return None

        # First check Python (.py) modules
        py_file_path = f"{mod_name}.py"
        if os.path.exists(py_file_path):
            try:
                py_globals = {}
                with open(py_file_path, "r", encoding="utf-8") as f:
                    exec(f.read(), py_globals)
                
                # Load Python functions and variables into the Ilus environment
                for k, v in py_globals.items():
                    if not k.startswith("__"):
                        if callable(v):
                            self.functions[k] = {"__type__": "py_function", "func": v}
                        else:
                            self.variables[k] = v
                self.log(f"[System]: '{mod_name}.py' (Python module) imported successfully.")
                return None
            except Exception as e:
                return f"Import error (Python): {str(e)}"

        # If .py doesn't exist, check local .ilus modules
        file_path = f"{mod_name}.ilus"
        if not os.path.exists(file_path):
            return f"Statistics error: local file '{mod_name}' not found (.py or .ilus)."
            
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                code = f.read()
            sub_interp = IlusInterpreter(self.request_input_callback)
            sub_interp.ilusaztar_imported = getattr(self, 'ilusaztar_imported', False)
            res = sub_interp.run(code)
            
            for l in sub_interp.logs:
                self.logs.append(l)
                
            if sub_interp.should_stop:
                return f"Error in imported file: {res}"

            self.variables.update(sub_interp.variables)
            module_only = getattr(sub_interp, '_module_only_names', set())
            for fname, fdata in sub_interp.functions.items():
                if fname in module_only:
                    self.functions[f"{mod_name}.{fname}"] = fdata
                else:
                    self.functions[fname] = fdata
            module_only_classes = getattr(sub_interp, '_module_only_class_names', set())
            for cname, cdata in sub_interp.classes.items():
                if cname in module_only_classes:
                    self.classes[f"{mod_name}.{cname}"] = cdata
                else:
                    self.classes[cname] = cdata
            if getattr(sub_interp, 'ilusaztar_imported', False):
                self.ilusaztar_imported = True
            self.imported_modules.add(mod_name)
            self.log(f"[System]: '{mod_name}.ilus' module imported successfully.")
            return None
        except Exception as e:
            return f"Import error: {str(e)}"

    def handle_import_multi(self, mod_names_str, line_num):
        """`import mod1, mod2, mod3` or `import mod1 and mod2` - each name is
        a completely independent module import (not related to each other) -
        ALL of them get imported, equivalent to writing `import mod1` then
        `import mod2` on separate lines.

        `import mod1 or mod2 [or mod3...]` is different: it's a fallback
        chain - try mod1 first, and only if that fails, try mod2, and so on.
        Only the FIRST one that successfully imports actually gets imported;
        the rest are never even attempted."""
        or_parts = re.split(r'\s+or\s+', mod_names_str)
        if len(or_parts) > 1:
            last_err = None
            for mod_name in [p.strip() for p in or_parts if p.strip()]:
                err = self.handle_import(mod_name, line_num)
                if err is None:
                    return None  # this one worked - stop, don't try the rest
                last_err = err
            return f"Statistics error: none of the modules in 'import {mod_names_str}' could be imported. Last error: {last_err}"

        and_parts = re.split(r'\s+and\s+', mod_names_str)
        if len(and_parts) > 1:
            mod_names_str = ",".join(p.strip() for p in and_parts)

        mod_names, unbal = self.smart_split(mod_names_str)
        if unbal: return "Statistics error: 'import' list is unbalanced (check quotes/parentheses)."
        mod_names = [m.strip() for m in mod_names if m.strip()]
        if not mod_names:
            return "Statistics error: 'import' requires at least one module name."
        for mod_name in mod_names:
            err = self.handle_import(mod_name, line_num)
            if err: return err
        return None

    def handle_from_import(self, mod_name, names_str, line_num):
        """`from module_name import name1, name2, name3` - imports only the
        specified functions/classes/variables from the module (under their
        bare names, no module prefix needed), instead of everything."""
        names, unbal = self.smart_split(names_str)
        if unbal: return "Statistics error: 'from ... import ...' list is unbalanced (check quotes/parentheses)."
        names = [n.strip() for n in names if n.strip()]
        if not names:
            return "Statistics error: 'from ... import ...' requires at least one name."

        if mod_name == "ilusaztar":
            return "Statistics error: 'ilusaztar' does not support 'from ... import ...' - use 'import ilusaztar' instead."

        # Python (.py) module: pull only the requested names out of its globals.
        py_file_path = f"{mod_name}.py"
        if os.path.exists(py_file_path):
            try:
                py_globals = {}
                with open(py_file_path, "r", encoding="utf-8") as f:
                    exec(f.read(), py_globals)
                missing = []
                for name in names:
                    if name not in py_globals or (isinstance(name, str) and name.startswith("__")):
                        missing.append(name)
                        continue
                    v = py_globals[name]
                    if callable(v):
                        self.functions[name] = {"__type__": "py_function", "func": v}
                    else:
                        self.variables[name] = v
                if missing:
                    return f"Import error: {', '.join(missing)} not found in '{mod_name}.py'."
                self.log(f"[System]: {', '.join(names)} imported from '{mod_name}.py'.")
                return None
            except Exception as e:
                return f"Import error (Python): {str(e)}"

        # .ilus module: run it once, then pull out only the requested names
        # (function, class, or variable - whichever matches) under their bare
        # names, whether or not they were declared @__module__ in the source.
        file_path = f"{mod_name}.ilus"
        if not os.path.exists(file_path):
            return f"Statistics error: local file '{mod_name}' not found (.py or .ilus)."

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                code = f.read()
            sub_interp = IlusInterpreter(self.request_input_callback)
            sub_interp.ilusaztar_imported = getattr(self, 'ilusaztar_imported', False)
            res = sub_interp.run(code)

            for l in sub_interp.logs:
                self.logs.append(l)

            if sub_interp.should_stop:
                return f"Error in imported file: {res}"

            missing = []
            for name in names:
                found = False
                if name in sub_interp.functions:
                    self.functions[name] = sub_interp.functions[name]
                    found = True
                if name in sub_interp.classes:
                    self.classes[name] = sub_interp.classes[name]
                    found = True
                if name in sub_interp.variables:
                    self.variables[name] = sub_interp.variables[name]
                    found = True
                if not found:
                    missing.append(name)

            if missing:
                return f"Import error: {', '.join(missing)} not found in '{mod_name}.ilus'."

            if getattr(sub_interp, 'ilusaztar_imported', False):
                self.ilusaztar_imported = True
            self.log(f"[System]: {', '.join(names)} imported from '{mod_name}.ilus'.")
            return None
        except Exception as e:
            return f"Import error: {str(e)}"

    def _resolve_move_targets(self, raw_target, identity_str):
        """raw_target is the target text with only surrounding parens stripped (quotes intact if
        present). If it was quoted, resolve by the object's 'identity' field (existing behavior).
        If it's a bare identifier (no quotes), resolve via named_objects/variables directly."""
        was_quoted = raw_target.startswith('"') or raw_target.startswith("'")
        if was_quoted:
            return [obj for obj in self.game_objects if obj.get("identity") == identity_str]
        candidate = self.named_objects.get(identity_str)
        if candidate is None:
            v = self.variables.get(identity_str)
            if isinstance(v, dict) and "type" in v and v in self.game_objects:
                candidate = v
        if candidate is not None:
            return [candidate]
        return [obj for obj in self.game_objects if obj.get("identity") == identity_str]

    def execute_game_working_logic(self, arg):
        arg = arg.strip()
        
        # random logic does not require the game library
        if arg.startswith("random:"):
            if "=" in arg and "coordinat" in arg:
                if not getattr(self, 'ilusaztar_imported', False):
                    return None, "The 'ilusaztar' module must be imported for game functions (coordinat random)!"
                r_match = re.search(r'^random:\s*"([a-zA-Z]+)"\s*=\s*coordinat$', arg)
                if not r_match: return None, 'Random coordinate format is wrong.'
                obj_type = r_match.group(1)
                if not self.current_obj or self.current_obj["type"] != obj_type: 
                    return None, "Logic error: Object type does not match."
                rx, ry = random.randint(10, 340), random.randint(10, 200)
                self.current_obj["x"], self.current_obj["y"] = rx, ry
                self.log(f"Game Engine: '{obj_type}' spawned at random coordinate ({rx}, {ry}).")
                return (rx, ry), None
            elif '"' in arg:
                l_match = re.search(r'^random:\s*"(\w+)"$', arg)
                if l_match:
                    l_name = l_match.group(1)
                    if l_name in self.variables and isinstance(self.variables[l_name], list):
                        if self.variables[l_name]:
                            return random.choice(self.variables[l_name]), None
                        else:
                            return None, "Logic error: List is empty."
                    return None, f"Logic error: List named '{l_name}' not found."
                return None, "Random format is wrong."
            else:
                nums_match = re.search(r'^random:\s*\((\s*-?\d+\s*|\s*[A-Za-z_]\w*\s*),(\s*-?\d+\s*|\s*[A-Za-z_]\w*\s*)\)$', arg)
                if nums_match:
                    parts = []
                    for part in nums_match.groups():
                        part = part.strip()
                        if part.lstrip('-').isdigit():
                            parts.append(int(part))
                        elif part in self.variables and isinstance(self.variables[part], int) and not isinstance(self.variables[part], bool):
                            parts.append(self.variables[part])
                        else:
                            return None, f"Logic error: '{part}' is not a valid number or int variable for random: range."
                    s, e = parts
                    return random.randint(s, e), None
                return None, "Random range format is wrong."

        # other non-random work commands require the game library
        if not getattr(self, 'ilusaztar_imported', False):
            return None, "The 'ilusaztar' module must be imported for game functions!"
            
        if arg.startswith("coordinat:"):
            coord_str = arg[10:].strip()
            parts = coord_str.split(',')
            if len(parts) == 2 and parts[0].strip().lstrip('-').isdigit() and parts[1].strip().lstrip('-').isdigit():
                x_val = int(parts[0].strip())
                y_val = int(parts[1].strip())
                if self.current_obj:
                    self.current_obj["x"] = x_val
                    self.current_obj["y"] = y_val
                return (x_val, y_val), None
            else:
                return None, "Logic error: Coordinate format is wrong. Example: coordinat: 27,44 (numbers only)"
        elif arg.startswith("gravity:"):
            g_str = arg[8:].strip()
            clean_str = g_str.lstrip('-').replace('.', '', 1)
            if clean_str.isdigit() and clean_str != "":
                if self.current_obj:
                    self.current_obj["gravity"] = True
                return float(g_str), None
            else:
                return None, "Logic error: Gravity value must be a number."
        elif arg.startswith("size:"):
            size_str = arg[5:].strip()
            parts = size_str.split(',')
            if len(parts) == 2 and parts[0].strip().lstrip('-').isdigit() and parts[1].strip().lstrip('-').isdigit():
                w_val = int(parts[0].strip())
                h_val = int(parts[1].strip())
                if self.current_obj:
                    self.current_obj["size"] = (w_val, h_val)
                return (w_val, h_val), None
            else:
                return None, "Logic error: Size format is wrong. Example: size: 50,50 (numbers only)"
        elif arg.startswith("move:"):
            move_body = arg[5:].strip()
            if "=" in move_body:
                target_part, coords_part = move_body.split("=", 1)
                raw_target = target_part.strip().strip('()')
                target_identity = raw_target.strip('"\'')
                coords_part = coords_part.strip()
                if not target_identity:
                    return None, "Logic error: move format is wrong. Example: move: \"entity\" = 100,0"
                coord_match = re.fullmatch(r'(-?\d+)\s*,\s*(-?\d+)', coords_part)
                if not coord_match:
                    return None, "Logic error: move coordinate format is wrong. Example: move: \"entity\" = 100,0 (numbers only)"
                dx, dy = int(coord_match.group(1)), int(coord_match.group(2))

                matched_objs = self._resolve_move_targets(raw_target, target_identity)
                if not matched_objs:
                    return None, f"Logic error: object with identity '{target_identity}' not found."
                for obj in matched_objs:
                    obj["move_dx"] = dx
                    obj["move_dy"] = dy
                    obj["is_player"] = True

                self.log(f"Game Engine: object '{target_identity}' movement step set to ({dx}, {dy}) per press.")
                return True, None

            raw_target = move_body.strip('()')
            target_identity = raw_target.strip('"\'')
            if not target_identity:
                return None, "Logic error: move format is wrong. Example: move: \"entity\""
            
            matched_objs = self._resolve_move_targets(raw_target, target_identity)
            if not matched_objs:
                return None, f"Logic error: object with identity '{target_identity}' not found."
            for obj in matched_objs:
                obj["is_player"] = True
                obj["gravity"] = True
            
            self.log(f"Game Engine: movement and/or gravity activated for object '{target_identity}'.")
            return True, None
        return arg, None

    def parse_inline_types(self, token):
        token = token.strip()
        type_match = re.fullmatch(r'(int|fround|str|bool|dict|list|tuple|file)\((.*)\)', token)
        if type_match:
            target_type = type_match.group(1)
            inner_val_str = type_match.group(2).strip()
            
            if target_type == "dict" and inner_val_str:
                args, unbal = self.smart_split(inner_val_str)
                if not unbal and len(args) == 2:
                    k_list, err1 = self.resolve_general_value(args[0], allow_raw_string=True)
                    v_list, err2 = self.resolve_general_value(args[1], allow_raw_string=True)
                    if not err1 and not err2 and isinstance(k_list, list) and isinstance(v_list, list):
                        return dict(zip(k_list, v_list)), None
                    return None, "Logic error: dict() function requires two lists (list)."
                elif not inner_val_str:
                    return {}, None

            if not inner_val_str:
                if target_type == "int": return 0, None
                if target_type == "fround": return 0.0, None
                if target_type == "str": return "", None
                if target_type == "bool": return False, None
                if target_type == "dict": return {}, None
                if target_type == "list": return [], None
                if target_type == "tuple": return (), None
                if target_type == "file": return None, "Logic error: file() type cannot be converted from empty data."
            
            if inner_val_str.startswith('"') and inner_val_str.endswith('"'):
                resolved_inner = inner_val_str[1:-1]
            else:
                resolved_inner, err = self.resolve_general_value(inner_val_str, allow_raw_string=True)
                if err: return None, err
                
            return self.cast_value(target_type, resolved_inner)
        return None, "Not a type function"

    def parse_builtin_functions(self, token):
        token = token.strip()
        func_match = re.fullmatch(r'(max|min|len|type|check|id|free|abs|round|open|zip|think|locals|globals|TYPE|setattr|getattr)\((.*)\)', token)
        if func_match:
            func_name = func_match.group(1)
            inner_val_str = func_match.group(2).strip()
            
            if func_name == "open":
                args, unbal = self.smart_split(inner_val_str)
                if unbal or len(args) != 2: return None, "Logic error: open() requires exactly 2 arguments: (filename, mode)."
                fn_val, err1 = self.resolve_general_value(args[0], allow_raw_string=True)
                if err1: return None, err1

                valid_modes = ["r", "r+", "w", "w+", "a", "a+", "rb", "rb+", "wb", "wb+", "ab", "ab+"]
                mode_raw = args[1].strip().strip('"').strip("'")
                if mode_raw in valid_modes:
                    mode_val = mode_raw
                else:
                    mode_val, err2 = self.resolve_general_value(args[1], allow_raw_string=True)
                    if err2: return None, err2
                    if mode_val not in valid_modes:
                        return None, f"Logic error: '{mode_val}' is not a valid open() mode. Use r, r+, w, w+, a, a+ (optionally with a trailing b for binary)."
                
                if mode_val in ["r", "r+", "rb"] and not os.path.exists(fn_val):
                    return None, f"File error: '{fn_val}' not found."
                
                try:
                    f = open(fn_val, mode_val, encoding="utf-8")
                    self.open_files.append(f)
                    return {"__type__": "file", "handle": f, "name": fn_val, "mode": mode_val}, None
                except Exception as e:
                    return None, f"File error: File operation failed. ({str(e)})"
            
            if func_name == "setattr":
                args, unbal = self.smart_split(inner_val_str)
                if unbal: return None, "Statistics error: parentheses inside setattr() are unbalanced."
                if len(args) != 3: return None, "Logic error: setattr() requires exactly 3 arguments: (instance, \"attr_name\", value)."
                inst_val, err1 = self.resolve_general_value(args[0], allow_raw_string=True)
                if err1: return None, err1
                if not (isinstance(inst_val, dict) and inst_val.get("__type__") == "instance"):
                    return None, "Logic error: setattr() can only be used on a class instance."
                name_val, err2 = self.resolve_general_value(args[1], allow_raw_string=True)
                if err2: return None, err2
                if not isinstance(name_val, str):
                    return None, "Logic error: setattr()'s attribute name must be a string."
                val, err3 = self.resolve_general_value(args[2], allow_raw_string=True)
                if err3: return None, err3
                inst_val["attrs"][name_val] = val
                return None, None

            if func_name == "getattr":
                args, unbal = self.smart_split(inner_val_str)
                if unbal: return None, "Statistics error: parentheses inside getattr() are unbalanced."
                if len(args) != 2: return None, "Logic error: getattr() requires exactly 2 arguments: (instance, \"attr_name\")."
                inst_val, err1 = self.resolve_general_value(args[0], allow_raw_string=True)
                if err1: return None, err1
                if not (isinstance(inst_val, dict) and inst_val.get("__type__") == "instance"):
                    return None, "Logic error: getattr() can only be used on a class instance."
                name_val, err2 = self.resolve_general_value(args[1], allow_raw_string=True)
                if err2: return None, err2
                if not isinstance(name_val, str):
                    return None, "Logic error: getattr()'s attribute name must be a string."
                if name_val not in inst_val["attrs"]:
                    return None, f"Logic error: instance of '{inst_val['__class__']}' has no attribute '{name_val}'."
                return inst_val["attrs"][name_val], None

            if func_name == "zip":
                args, unbal = self.smart_split(inner_val_str)
                if unbal: return None, "Statistics error: parentheses inside zip() are unbalanced."
                
                if len(args) == 1:
                    v1, err1 = self.resolve_general_value(args[0], allow_raw_string=True)
                    if err1: return None, err1
                    if isinstance(v1, dict) and v1.get("__type__") not in ["instance", "file", "game_score"]:
                        formatted = ", ".join([f"{k}:{v}" for k, v in v1.items()])
                        return formatted, None
                    return None, "Logic error: single-argument zip() only accepts a dict."
                elif len(args) == 2:
                    v1, err1 = self.resolve_general_value(args[0], allow_raw_string=True)
                    v2, err2 = self.resolve_general_value(args[1], allow_raw_string=True)
                    if err1 or err2: return None, err1 or err2
                    
                    if isinstance(v1, dict) and v1.get("__type__") == "file" and isinstance(v2, dict) and v2.get("__type__") == "file":
                        return "<files is combined in one file>", None

                    if isinstance(v1, (list, tuple)) and isinstance(v2, (list, tuple)):
                        min_len = min(len(v1), len(v2))
                        result = []
                        for i in range(min_len):
                            result.append(v1[i]); result.append(v2[i])
                        return result, None

                    is_plain_dict = lambda d: isinstance(d, dict) and "__type__" not in d
                    if is_plain_dict(v1) and is_plain_dict(v2):
                        keys1, keys2 = list(v1.keys()), list(v2.keys())
                        vals1, vals2 = list(v1.values()), list(v2.values())
                        min_k, min_v = min(len(keys1), len(keys2)), min(len(vals1), len(vals2))
                        result = []
                        for i in range(min_k):
                            result.append(keys1[i]); result.append(keys2[i])
                        for i in range(min_v):
                            result.append(vals1[i]); result.append(vals2[i])
                        return result, None
                        
                    t1, t2 = type(v1), type(v2)
                    if t1 != t2:
                        if t1 in (int, float) and t2 in (int, float):
                            return v1 + v2, None
                        return None, f"Logic error: types {t1.__name__} and {t2.__name__} cannot be combined with zip()!"
                    
                    if isinstance(v1, (int, float, str)): return v1 + v2, None
                    else: return None, "Logic error: these types cannot be combined with zip()."
                else:
                    return None, "Logic error: zip() requires 1 or 2 arguments."

            if func_name == "think":
                args, unbal = self.smart_split(inner_val_str)
                if unbal or len(args) != 2: return None, "Logic error: think() requires exactly 2 arguments."
                v1, err1 = self.resolve_general_value(args[0], allow_raw_string=True)
                if err1: return None, err1
                
                def get_t(v):
                    if isinstance(v, bool): return "bool"
                    elif isinstance(v, int): return "int"
                    elif isinstance(v, float): return "fround"
                    elif isinstance(v, str): return "str"
                    elif isinstance(v, list): return "list"
                    elif isinstance(v, tuple): return "tuple"
                    elif isinstance(v, dict):
                        if "__type__" in v:
                            if v["__type__"] == "instance": return "instance"
                            elif v["__type__"] == "file": return "file"
                        return "dict"
                    return "unknown"
                
                raw_type = args[1].strip().strip('\'"')
                if raw_type in ["int", "str", "bool", "fround", "list", "dict", "tuple", "file", "instance"]:
                    expected_type = raw_type
                else:
                    v2, err2 = self.resolve_general_value(args[1], allow_raw_string=True)
                    if err2: return None, err2
                    expected_type = get_t(v2)
                    
                actual_type = get_t(v1)
                return actual_type == expected_type, None

            if func_name == "TYPE":
                if inner_val_str.startswith('"') and inner_val_str.endswith('"'): resolved_inner = inner_val_str[1:-1]
                else:
                    resolved_inner, err = self.resolve_general_value(inner_val_str, allow_raw_string=True)
                    if err: return None, err
                return {"__ilus_type_marker__": str(resolved_inner)}, None

            if func_name == "globals":
                if self._scope_stack:
                    outer_names = self._scope_stack[0]
                    return {k: v for k, v in self.variables.items() if k in outer_names}, None
                return dict(self.variables), None

            if func_name == "locals":
                if self._scope_stack:
                    outer_names = self._scope_stack[-1]
                    return {k: v for k, v in self.variables.items() if k not in outer_names}, None
                return dict(self.variables), None
            
            if func_name == "free":
                prompt = ""  
                if inner_val_str:
                    if inner_val_str.startswith('"') and inner_val_str.endswith('"'): prompt = inner_val_str[1:-1]
                    elif inner_val_str.startswith('("') and inner_val_str.endswith('")'): prompt = inner_val_str[2:-2]
                    else:
                        p_val, p_err = self.resolve_general_value(inner_val_str, allow_raw_string=True)
                        if not p_err: prompt = str(p_val)
                # free("...") right after creating an Input/InputBox/Text field sets its
                # placeholder text immediately - it shouldn't need to wait for a submit event.
                if prompt and self.current_obj and self.current_obj.get("gui_type") in ("Input", "InputBox", "Text"):
                    self.current_obj["_input_prompt"] = prompt
                if self.pending_input_value is not None:
                    if prompt and self.active_input_object is not None:
                        self.active_input_object["_input_prompt"] = prompt
                    user_input = self.pending_input_value
                    self.pending_input_value = None
                elif self.request_input_callback: user_input = self.request_input_callback(prompt)
                else: user_input = ""
                return user_input, None

            if func_name == "check":
                ivs = inner_val_str.strip()
                if ivs.endswith("()"):
                    base_name = ivs[:-2]
                    if base_name in ["write", "work", "type", "check", "len", "max", "min", "int", "fround", "str", "bool", "id", "free", "dict", "list", "tuple", "abs", "round", "open", "zip", "think", "locals", "globals", "TYPE", "setattr", "getattr"]:
                        return f"{base_name}() in #method class", None
                if ivs in ["int", "fround", "str", "bool", "dict", "list", "tuple"]: return f"{ivs} in #{ivs} class", None
                if ivs in ILUS_KEYWORDS: return f"{ivs} in #keyword class", None
                if ivs in ["work Sx.game", "work Area.game"]: return "slot.game in #statement class", None
                if ivs.startswith("Create:") or ivs.startswith("Color:") or ivs.startswith("Set:") or ivs.startswith("Size:") or ivs.startswith("Text:") or ivs.startswith("Text.Color:") or ivs.startswith("Text.Size:") or ivs.startswith("UI(") or ivs.startswith("Score:"):
                    return "object.game in #statement class", None
                if ivs.startswith("Repeat:"): return "loop in #statement class", None
                if ivs in self.variables:
                    val = self.variables[ivs]
                    py_class = type(val).__name__
                    origin = self.variable_origins.get(ivs)
                    if origin: t_name = origin
                    else:
                        if isinstance(val, bool): t_name = "bool"
                        elif isinstance(val, int): t_name = "int"
                        elif isinstance(val, float): t_name = "fround"
                        elif isinstance(val, str): t_name = "str"
                        elif isinstance(val, list): t_name = "list"
                        elif isinstance(val, tuple): t_name = "tuple"
                        elif isinstance(val, dict):
                            if "__type__" in val:
                                if val["__type__"] in ["instance", "game_score"]: t_name = val["__class__"] if "__class__" in val else val["__type__"]
                                elif val["__type__"] == "file": t_name = "file"
                            elif "type" in val: t_name = "game_object"
                            else: t_name = "dict"
                        else: t_name = "unknown"
                    if isinstance(val, dict) and val.get("__type__") in ["instance", "game_score"]:
                        return f"{ivs} in class {t_name}", None
                    return f"{t_name} in #{py_class} class", None
                if ivs.startswith('"') and ivs.endswith('"'): return f"str in #{type(ivs[1:-1]).__name__} class", None
                if "random:" in ivs: return "random in #int class", None
                if "coordinat:" in ivs: return "coordinat in #tuple class", None
                val, err = self.resolve_general_value(ivs, allow_raw_string=True)
                if err: return None, err
                py_class = type(val).__name__
                if isinstance(val, bool): t_name = "bool"
                elif isinstance(val, int): t_name = "int"
                elif isinstance(val, float): t_name = "fround"
                elif isinstance(val, str): t_name = "str"
                elif isinstance(val, list): t_name = "list"
                elif isinstance(val, tuple): t_name = "tuple"
                elif isinstance(val, dict): 
                    if "__type__" in val:
                        if val["__type__"] in ["instance", "game_score"]: t_name = val["__class__"] if "__class__" in val else val["__type__"]
                        elif val["__type__"] == "file": t_name = "file"
                    else: t_name = "dict"
                else: t_name = "unknown"
                if isinstance(val, dict) and val.get("__type__") in ["instance", "game_score"]:
                    return f"{ivs} in class {t_name}", None
                return f"{t_name} in #{py_class} class", None

            if inner_val_str.startswith('"') and inner_val_str.endswith('"'): resolved_inner = inner_val_str[1:-1]
            else:
                resolved_inner, err = self.resolve_general_value(inner_val_str, allow_raw_string=True)
                if err: return None, err
            
            if func_name == "abs":
                if isinstance(resolved_inner, (int, float)) and not isinstance(resolved_inner, bool):
                    return abs(resolved_inner), None
                return None, "Logic error: abs() function only accepts 'int' or 'fround' types."
            elif func_name == "round":
                if isinstance(resolved_inner, (int, float)) and not isinstance(resolved_inner, bool):
                    return int(resolved_inner + 0.5) if resolved_inner >= 0 else int(resolved_inner - 0.5), None
                return None, "Logic error: round() function only accepts 'int' or 'fround' types."
            elif func_name == "max":
                if not isinstance(resolved_inner, list): return None, "Logic error: max() function can only be used with lists."
                if not resolved_inner: return None, "Logic error: List is empty."
                if all(isinstance(x, (int, float, str)) for x in resolved_inner): return max(resolved_inner), None
                return None, "Logic error: elements inside the list cannot be compared."
            elif func_name == "min":
                if not isinstance(resolved_inner, list): return None, "Logic error: min() function can only be used with lists."
                if not resolved_inner: return None, "Logic error: List is empty."
                if all(isinstance(x, (int, float, str)) for x in resolved_inner): return min(resolved_inner), None
                return None, "Logic error: elements inside the list cannot be compared."
            elif func_name == "len":
                if isinstance(resolved_inner, bool): return None, "Error: len() must not be used with boolean (True/False) types!"
                elif isinstance(resolved_inner, (int, float)):
                    str_val = str(resolved_inner).replace('.', '').lstrip('-')
                    return len(str_val), None
                elif isinstance(resolved_inner, (str, list, tuple, dict)): return len(resolved_inner), None
                else: return None, "Error: len() used for an unsupported type."
            elif func_name == "type":
                var_origin = self.variable_origins.get(inner_val_str.strip())
                if var_origin == "int" and isinstance(resolved_inner, list):
                    t_name = "int"
                elif isinstance(resolved_inner, bool): t_name = "bool"
                elif isinstance(resolved_inner, int): t_name = "int"
                elif isinstance(resolved_inner, float): t_name = "fround"
                elif isinstance(resolved_inner, str): t_name = "str"
                elif isinstance(resolved_inner, list): t_name = "list"
                elif isinstance(resolved_inner, tuple): t_name = "tuple"
                elif isinstance(resolved_inner, dict):
                    if "__type__" in resolved_inner:
                        if resolved_inner["__type__"] in ["instance", "game_score"]: t_name = resolved_inner["__class__"] if "__class__" in resolved_inner else resolved_inner["__type__"]
                        elif resolved_inner["__type__"] == "file": t_name = "file"
                    elif "type" in resolved_inner: t_name = "game_object"
                    else: t_name = "dict"
                else: t_name = "unknown"
                return f"<{t_name} class>", None
            elif func_name == "id":
                addr = hex(id(resolved_inner))
                return f"#{addr}", None
            # New feature #1: zip() - pairs elements of the same type
            elif func_name == "zip":
                if not isinstance(resolved_inner, (list, tuple)): 
                    return None, "Statistics error: zip() only works with list/tuple."
                
                if len(resolved_inner) < 2:
                    return None, "Statistics error: zip() requires at least 2 lists/tuples."
                
                # check that all elements are of the same type
                result = []
                items_to_zip = []
                
                for item in resolved_inner:
                    if isinstance(item, (list, tuple, dict)):
                        items_to_zip.append(item)
                    else:
                        return None, "Statistics error: zip() only works with list/tuple/dict."
                
                # pair elements of the same type
                if len(items_to_zip) >= 2:
                    min_len = min(len(x) for x in items_to_zip if isinstance(x, (list, tuple)))
                    for i in range(min_len):
                        pair = tuple(x[i] for x in items_to_zip if isinstance(x, (list, tuple)))
                        result.append(pair)
                
                return result, None
        return None, "Not a builtin function"

    def _check_param_type(self, param, val, owner_name):
        ptype = param.get("type")
        if not ptype: return None
        py_types = {"int": int, "str": str, "fround": float, "bool": bool, "list": list, "dict": dict, "tuple": tuple}
        expected = py_types[ptype]
        type_ok = isinstance(val, expected) and not (ptype != "bool" and isinstance(val, bool))
        if ptype == "fround" and isinstance(val, int) and not isinstance(val, bool):
            type_ok = True
        if not type_ok:
            return f"Logic error: parameter '{param['name']}' of '{owner_name}' is declared as type '{ptype}', but the given value is of type '{type(val).__name__}'."
        return None

    def cast_value(self, target_type, value):
        if target_type not in ["int", "str", "bool", "fround", "dict", "list", "tuple", "file"]: 
            return None, "Statistics error: no such Ilus type exists."
        
        is_file = isinstance(value, dict) and value.get("__type__") == "file"
        
        if is_file and target_type != "file":
            return None, f"Logic error: file object cannot be converted to type {target_type}() 💀."
            
        if target_type == "file":
            if is_file: return value, None
            else: return None, "Logic error: other types cannot be converted to file() type 💀."
            
        if target_type in ["int", "fround", "bool"] and isinstance(value, (list, tuple)):
            res = []
            for item in value:
                val, err = self.cast_value(target_type, item)
                if err: return None, f"Logic error: element inside List/Tuple cannot be converted to type '{target_type}'."
                res.append(val)
            return (IlusConvertedList(res) if isinstance(value, list) else IlusConvertedTuple(res)), None

        if target_type == "str":
            if isinstance(value, dict) and value.get("__type__") == "instance":
                cls_name = value["__class__"]
                if "__str__" in self.classes[cls_name]["methods"]:
                    res, err = self.execute_method_with_values(value, cls_name, "__str__", [])
                    if not err: return str(res), None
                return f"<instance of {cls_name}>", None
            if is_file: return f"<file '{value.get('name')}' mode '{value.get('mode')}'>", None
            if isinstance(value, list): return "\n".join(str(item) for item in value), None
            if isinstance(value, tuple): return "\n".join(str(item) for item in value), None
            return str(value), None

        elif target_type == "int":
            if isinstance(value, bool): return int(value), None
            elif isinstance(value, str):
                if ' ' in value or ',' in value: 
                    return None, f"Logic error: '{value}' contains an obstacle (space/comma) and cannot be converted to int() type."
                if value.lstrip('-').isdigit(): return int(value), None
                else: return None, f"Logic error: '{value}' contains text and cannot be converted to int() type."
            elif isinstance(value, (int, float)): return int(value), None
            else: return None, f"Logic error: data cannot be converted to int() type."
            
        elif target_type == "fround":
            if isinstance(value, bool): 
                return None, "Logic error: bool() cannot be converted to fround() type 💀"
            elif isinstance(value, str):
                if ' ' in value or ',' in value: 
                    return None, f"Logic error: '{value}' contains an obstacle (space/comma) and cannot be converted to fround() type."
                clean_str = value.lstrip('-').replace('.', '', 1)
                if clean_str.isdigit() and clean_str != "": return float(value), None
                else: return None, f"Logic error: '{value}' contains text and cannot be converted to fround() type."
            elif isinstance(value, (int, float)): return float(value), None
            else: return None, f"Logic error: data cannot be converted to fround() type."
            
        elif target_type == "bool":
            if isinstance(value, float):
                return None, "Logic error: fround() cannot be converted to bool() type 💀"
            if str(value) in ["True", "1"]: return True, None
            elif str(value) in ["False", "0"]: return False, None
            elif str(value) in ["true", "false", "none"]: return None, f"Statistics error: lowercase letters '{value}' cannot be counted as bool function!"
            elif isinstance(value, str): return len(value) > 0, None
            else: return bool(value), None
            
        elif target_type == "list":
            if isinstance(value, dict) and value.get("__type__") not in ["instance", "file"]:
                res = []
                for k, v in value.items(): res.extend([k, v])
                return res, None
            elif isinstance(value, (list, tuple)): return list(value), None
            elif isinstance(value, str): return [value], None
            else: return [value], None
            
        elif target_type == "tuple":
            if isinstance(value, dict) and value.get("__type__") not in ["instance", "file"]:
                res = []
                for k, v in value.items(): res.extend([k, v])
                return tuple(res), None
            elif isinstance(value, (list, tuple)): return tuple(value), None
            elif isinstance(value, str): return (value,), None
            else: return (value,), None

        elif target_type == "dict":
            if isinstance(value, dict) and value.get("__type__") not in ["instance", "file"]: return dict(value), None
            elif isinstance(value, (list, tuple)):
                if len(value) > 0 and isinstance(value[0], (list, tuple)) and len(value[0]) == 2:
                    is_valid = True
                    for item in value:
                        if not (isinstance(item, (list, tuple)) and len(item) == 2):
                            is_valid = False
                            break
                    if is_valid: return dict(value), None
                    else: return dict(enumerate(value)), None
                else:
                    return dict(enumerate(value)), None
            elif isinstance(value, str): return {0: value}, None
            else: return {"value": value}, None

        return None, "Logic error: unknown type error."

    def evaluate_single_condition(self, sub_cond):
        op = None
        for possible_op in ["==", "!=", "<=", ">=", "<", ">"]:
            if possible_op in sub_cond:
                op = possible_op
                break
        if not op:
            val, err = self.resolve_cond_value(sub_cond)
            if not err and isinstance(val, bool): return val, None
            return None, f"Statistics error: correct operator not found in condition: '{sub_cond}'"
            
        parts = sub_cond.split(op, 1)
        left_str, right_str = parts[0].strip(), parts[1].strip()
        if not left_str or not right_str: return None, "Statistics error: operator cannot be empty."

        type_match = re.fullmatch(r'(int|fround|str|bool|dict|list|tuple)\(\)', right_str)
        if type_match and op in ["==", "!="]:
            expected_type = type_match.group(1)
            left_val, err_l = self.resolve_cond_value(left_str)
            if err_l: return None, err_l
            
            if isinstance(left_val, bool): actual_type = "bool"
            elif isinstance(left_val, int): actual_type = "int"
            elif isinstance(left_val, float): actual_type = "fround"
            elif isinstance(left_val, str): actual_type = "str"
            elif isinstance(left_val, list): actual_type = "list"
            elif isinstance(left_val, tuple): actual_type = "tuple"
            elif isinstance(left_val, dict): 
                if left_val.get("__type__") == "instance": actual_type = "instance"
                elif left_val.get("__type__") == "file": actual_type = "file"
                else: actual_type = "dict"
            else: actual_type = "unknown"
            
            if op == "==": return actual_type == expected_type, None
            elif op == "!=": return actual_type != expected_type, None

        left_val, err_l = self.resolve_cond_value(left_str)
        if err_l: return None, err_l
        right_val, err_r = self.resolve_cond_value(right_str)
        if err_r: return None, err_r

        t1, t2 = type(left_val), type(right_val)
        if op in ["<=", ">=", "<", ">"] and t1 != t2: 
            return None, f"Logic error: cannot be compared ({left_val} and {right_val})."

        if op == "==":
            if isinstance(right_val, dict) and "__ilus_type_marker__" in right_val:
                return isinstance(left_val, dict) and left_val.get("type") == right_val["__ilus_type_marker__"], None
            if isinstance(left_val, dict) and "__ilus_type_marker__" in left_val:
                return isinstance(right_val, dict) and right_val.get("type") == left_val["__ilus_type_marker__"], None
            return left_val == right_val, None
        elif op == "!=":
            if isinstance(right_val, dict) and "__ilus_type_marker__" in right_val:
                return not (isinstance(left_val, dict) and left_val.get("type") == right_val["__ilus_type_marker__"]), None
            if isinstance(left_val, dict) and "__ilus_type_marker__" in left_val:
                return not (isinstance(right_val, dict) and right_val.get("type") == left_val["__ilus_type_marker__"]), None
            return left_val != right_val, None
        elif op == "<=": return left_val <= right_val, None
        elif op == ">=": return left_val >= right_val, None
        elif op == "<": return left_val < right_val, None
        elif op == ">": return left_val > right_val, None
        return None, "Unknown operator"

    def evaluate_condition(self, cond_str):
        cond_str = cond_str.replace("=>", ">=")
        macro_match = re.match(r'^(\w+)\s*%\s*(\d+)\s+(and|or)\s+%\s*(\d+)\s*==\s*(\d+)$', cond_str.strip())
        if macro_match:
            var, m1, op, m2, val = macro_match.groups()
            cond_str = f"{var} % {m1} == {val} {op} {var} % {m2} == {val}"
        
        if " and " in cond_str:
            sub_parts = cond_str.split(" and ")
            if all(any(op in p for op in ["==", "!=", "<=", ">=", "<", ">", " is "]) for p in sub_parts):
                results = []
                for p in sub_parts:
                    res, err = self.evaluate_single_condition(p.strip())
                    if err: return None, err
                    results.append(res)
                return all(results), None
                
        if " or " in cond_str:
            sub_parts = cond_str.split(" or ")
            if all(any(op in p for op in ["==", "!=", "<=", ">=", "<", ">", " is "]) for p in sub_parts):
                results = []
                for p in sub_parts:
                    res, err = self.evaluate_single_condition(p.strip())
                    if err: return None, err
                    results.append(res)
                return any(results), None

        op = None
        for possible_op in ["==", "!=", "<=", ">=", "<", ">"]:
            if possible_op in cond_str:
                op = possible_op
                break
        if not op: 
            val, err = self.resolve_cond_value(cond_str)
            if not err and isinstance(val, bool): return val, None
            if err: return None, err
            return None, "Statistics error: correct operator not found in condition."
            
        parts = cond_str.split(op, 1)
        left_str, right_str = parts[0].strip(), parts[1].strip()
        if not left_str or not right_str: return None, "Statistics error: operator cannot be empty."

        type_match = re.fullmatch(r'(int|fround|str|bool|dict|list|tuple)\(\)', right_str)
        if not type_match:
            type_match = re.fullmatch(r'type\((int|fround|str|bool|dict|list|tuple)\)', right_str)
        is_type_check = (type_match and op in ["==", "!="])

        if not is_type_check:
            right_val, err_r = self.resolve_cond_value(right_str)
            if err_r: return None, err_r
        else:
            expected_type = type_match.group(1)

        if " or " in left_str:
            left_items = [item.strip() for item in left_str.split(" or ")]
            mode = "or"
        elif " and " in left_str:
            left_items = [item.strip() for item in left_str.split(" and ")]
            mode = "and"
        else:
            left_items = [left_str]
            mode = "single"

        results = []
        for item in left_items:
            left_val, err_l = self.resolve_cond_value(item)
            if err_l: return None, err_l
            
            if is_type_check:
                if isinstance(left_val, bool): actual_type = "bool"
                elif isinstance(left_val, int): actual_type = "int"
                elif isinstance(left_val, float): actual_type = "fround"
                elif isinstance(left_val, str): actual_type = "str"
                elif isinstance(left_val, list): actual_type = "list"
                elif isinstance(left_val, tuple): actual_type = "tuple"
                elif isinstance(left_val, dict): 
                    if left_val.get("__type__") == "instance": actual_type = "instance"
                    elif left_val.get("__type__") == "file": actual_type = "file"
                    else: actual_type = "dict"
                else: actual_type = "unknown"
                
                if op == "==": results.append(actual_type == expected_type)
                elif op == "!=": results.append(actual_type != expected_type)
            else:
                t1, t2 = type(left_val), type(right_val)
                if op in ["<=", ">=", "<", ">"] and t1 != t2: 
                    return None, f"Logic error: cannot be compared ({left_val} and {right_val})."

                if op == "==":
                    if isinstance(right_val, dict) and "__ilus_type_marker__" in right_val:
                        results.append(isinstance(left_val, dict) and left_val.get("type") == right_val["__ilus_type_marker__"])
                    else:
                        results.append(left_val == right_val)
                elif op == "!=":
                    if isinstance(right_val, dict) and "__ilus_type_marker__" in right_val:
                        results.append(not (isinstance(left_val, dict) and left_val.get("type") == right_val["__ilus_type_marker__"]))
                    else:
                        results.append(left_val != right_val)
                elif op == "<=": results.append(left_val <= right_val)
                elif op == ">=": results.append(left_val >= right_val)
                elif op == "<": results.append(left_val < right_val)
                elif op == ">": results.append(left_val > right_val)

        if mode == "or": return any(results), None
        elif mode == "and": return all(results), None
        else: return results[0], None

    def resolve_cond_value(self, val_str):
        val_str = val_str.strip()
        
        if val_str.startswith('("') and val_str.endswith('")') and val_str.count('"') >= 2: 
            inner_raw = val_str[1:-1].strip()
            if re.fullmatch(r'^"[^"]*"$', inner_raw) or re.fullmatch(r'^(\s*"[^"]*"\s*)+$', inner_raw):
                if re.fullmatch(r'^"[^"]*"$', inner_raw): return inner_raw[1:-1], None
                return "".join(re.findall(r'"([^"]*)"', inner_raw)), None
            
        if val_str in self.variables: return self.variables[val_str], None
        
        if val_str == "True": return True, None
        elif val_str == "False": return False, None
        elif val_str == "None": return None, None
        elif val_str in ["true", "false", "none"]:
            return None, f"Statistics error: '{val_str}' cannot be counted as bool function! Correct usage: {val_str.capitalize()}"
        
        if val_str.startswith('work(') and val_str.endswith(')'):
            inner_cmd = val_str[5:-1].strip()
            if inner_cmd.startswith('working(') and inner_cmd.endswith(')'):
                work_arg = inner_cmd[8:-1].strip().strip('"')
                return self.execute_game_working_logic(work_arg)

        cond_clean = val_str
        if val_str.startswith("table."): cond_clean = val_str[6:]
        if re.fullmatch(r'^([\w.]+)\[(.*)\]$', cond_clean):
            val, err = self.resolve_general_value(val_str, allow_raw_string=True)
            if not err: return val, None
            return None, err

        val, err = self.parse_inline_types(val_str)
        if err != "Not a type function": return val, err

        val, err = self.parse_builtin_functions(val_str)
        if err != "Not a builtin function": return val, err

        fn_match = re.match(r'^(\*?[\w.]+)\((.*)\)$', val_str)
        if fn_match and fn_match.group(1) not in ["int", "fround", "str", "bool", "max", "min", "len", "type", "check", "id", "free", "dict", "list", "tuple", "abs", "round", "open", "zip", "think", "locals", "globals", "TYPE", "setattr", "getattr"]:
            val, err = self.execute_possibly_chained_call(val_str, 0)
            if not err: return val, None
            return None, err

        if val_str.replace('.', '', 1).lstrip('-').isdigit():
            if '.' in val_str: return float(val_str), None
            return int(val_str), None
            
        evaluator = SafeMathEvaluator(self.variables, self)
        res = evaluator.evaluate(val_str)
        if not evaluator.error: return res, None
        return None, f"Logic error: comparison target '{val_str}' not found or is an error. {evaluator.error}"

    def smart_split(self, content):
        content_str = content.strip()
        if not content_str: return [], False
        if content_str.endswith(',') or content_str.startswith(','): return [], True
            
        args = []
        current_arg = []
        in_quotes = False
        bracket_depth, paren_depth, brace_depth = 0, 0, 0
        
        for char in content_str:
            if char == '"':
                in_quotes = not in_quotes
                current_arg.append(char)
            elif not in_quotes and char == '[':
                bracket_depth += 1
                current_arg.append(char)
            elif not in_quotes and char == ']':
                bracket_depth -= 1
                current_arg.append(char)
            elif not in_quotes and char == '{':
                brace_depth += 1
                current_arg.append(char)
            elif not in_quotes and char == '}':
                brace_depth -= 1
                current_arg.append(char)
            elif not in_quotes and char == '(':
                paren_depth += 1
                current_arg.append(char)
            elif not in_quotes and char == ')':
                paren_depth -= 1
                current_arg.append(char)
            elif char == ',' and not in_quotes and bracket_depth == 0 and paren_depth == 0 and brace_depth == 0:
                arg_val = "".join(current_arg).strip()
                if not arg_val: return [], True 
                args.append(arg_val)
                current_arg = []
            else:
                current_arg.append(char)
                
        if current_arg: 
            arg_val = "".join(current_arg).strip()
            if not arg_val: return [], True
            args.append(arg_val)
            
        return args, (in_quotes or bracket_depth != 0 or paren_depth != 0 or brace_depth != 0)

    def check_line_syntax_only(self, line):
        line = line.strip()
        if not line: return True
        if line == "@__module__": return True

        if line.startswith("write(") and line.endswith(")"):
            depth = 0
            for ch in line[5:]:
                if ch == '(': depth += 1
                elif ch == ')': depth -= 1
            if depth == 0: return True
            return False

        parent_match = re.match(r'^(\w+)\.(Create:|Size:|Color:|Text:|Shape:|Text\.Color:|Text\.Size:|Set:|Score:|Score\.Color:|Score\.Size:|Anchored:|Collision:|Transparency:)', line)
        if parent_match and parent_match.group(1) not in ("Text", "Score"):
            return self.check_line_syntax_only(line[len(parent_match.group(1)) + 1:])

        _, unbal = self.smart_split(line)
        if unbal: return False

        if line.startswith("import "): return True
        if re.match(r'^from\s+[\w.]+\s+import\s+.+$', line): return True
        if line.startswith("wait(") and line.endswith(")"): return True

        if line in ["continue", "break", "pass"]: return True
        if line.startswith("break "): return True
        if line.startswith("del "): return True
        if line.startswith("Score:"): return re.fullmatch(r'Score:\s*\(\#+\)', line) is not None
        
        if line.startswith("class "): return re.match(r'^class\s+([\w.]+)(?:\(([\w_.]+)\))?:$', line) is not None
        if line.startswith("function "): return re.match(r'^function\s+([\w_]+)\(([^)]*)\):$', line) is not None
        if line.startswith("return ") or line == "return": return True
        if line.startswith("Target "): return line.endswith(":")
        if line.startswith("Choose "): return line.endswith(":")
        if line == "Fail:": return True
        if line in ["work Sx.game", "work Area.game"]: return True

        _parent_m = re.match(r'^[A-Za-z_]\w*\.((?:Text\.Color|Text\.Size|Score\.Size|Score\.Color|Create|Size|Color|Text|Set|Score|Anchored|Transparency|Collision):.*)$', line)
        if _parent_m:
            return self.check_line_syntax_only(_parent_m.group(1))
        
        if line.startswith("Create:"): return re.fullmatch(r'Create:\s*"(block|circle|UI|area|GUI)"', line) is not None
        if line.startswith("Set:"):
            return (re.fullmatch(r'Set:\s*"(entity|object)"', line) is not None or
                    re.fullmatch(r'Set:\s*"(entity|object)"\s*=\s*user\.id\s*=\s*(-?\d+|int)', line) is not None)
        if line.startswith("move:"): return re.fullmatch(r'move:\s*"[^"]+"(?:\s*=\s*-?\d+\s*,\s*-?\d+)?', line) is not None

        # A quoted string value: "..." (canonical) or ("...") — both forms the executor accepts.
        _QSTR = r'(?:"[^"\\]*(?:\\.[^"\\]*)*"|\("[^"\\]*(?:\\.[^"\\]*)*"\))'
        # A color/text value: a quoted string, or a variable/dotted-attribute reference.
        _VAL = rf'(?:{_QSTR}|[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)'
        # A size value: Width_Height (digits), a single number, or a variable reference.
        _SIZE = r'(?:-?\d+\s*_\s*-?\d+|[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*|-?\d+)'
        # Strict quoted string: literal strings must be parenthesized — ("...") only.
        _QSTR_PAREN = r'\("[^"\\]*(?:\\.[^"\\]*)*"\)'
        # Strict value: a parenthesized string, or a variable/dotted-attribute reference.
        _VAL_STRICT = rf'(?:{_QSTR_PAREN}|[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)'

        if line.startswith("Text.Color:"): return re.fullmatch(rf'Text\.Color:\s*{_VAL}', line) is not None
        if line.startswith("Text.Size:"): return re.fullmatch(rf'Text\.Size:\s*{_SIZE}', line) is not None
        if line.startswith("Text:"): return re.fullmatch(rf'Text:\s*{_VAL_STRICT}', line) is not None
        if line.startswith("Score.Color:"): return re.fullmatch(rf'Score\.Color:\s*{_VAL}', line) is not None
        if line.startswith("Score.Size:"): return re.fullmatch(rf'Score\.Size:\s*{_SIZE}', line) is not None
        if line.startswith("Color:"): return re.fullmatch(rf'Color:\s*{_VAL}', line) is not None
        if line.startswith("Size:"): return re.fullmatch(rf'Size:\s*{_SIZE}', line) is not None
        if line.startswith(("Anchored:", "Collision:")):
            val_str = line.split(":", 1)[1].strip()
            return re.fullmatch(r'(?:True|False|[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)', val_str) is not None
        if line.startswith("Transparency:"):
            val_str = line.split(":", 1)[1].strip()
            return re.fullmatch(r'(?:-?\d+(?:\.\d+)?|[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)', val_str) is not None
        if line.startswith("UI("): return re.fullmatch(r'UI\((left|right|jump|up|down)(?:\s*,\s*Bind\s*=\s*[A-Za-z_]\w*)?\)', line) is not None
        if line.startswith("Repeat:"): return re.search(r'^Repeat:\s*\((.*)\)$', line) is not None

        if line.startswith("GUI("):
            # Each parameter must be a known key (Text/Bind/Size/Color) with a non-empty value,
            # OR (for GUI(Image, "...")) a bare quoted filename.
            _GUI_TEXT = rf'Text\s*=\s*{_QSTR}'
            _GUI_BIND = r'Bind\s*=\s*[A-Za-z_]\w*'
            _GUI_SIZE = rf'Size\s*=\s*{_SIZE}'
            _GUI_COLOR = rf'Color\s*=\s*{_VAL}'
            _GUI_PARAM = rf'(?:{_GUI_TEXT}|{_GUI_BIND}|{_GUI_SIZE}|{_GUI_COLOR}|{_QSTR})'
            return re.fullmatch(rf'GUI\((Button|Text|Label|InputBox|Input|GUI|Image)(?:\s*,\s*{_GUI_PARAM})*\)', line) is not None

        if re.match(r'^(\*?[\w.]+)\((.*)\)$', line): return True
        if re.search(r'\s*(\+=|-=|\*\*=|\*=|//=|/=|%=|\^=)\s*', line): return True
            
        assign_parts = re.split(r'(?<![=!<>])=(?![=])', line)
        if len(assign_parts) >= 2:
            left_side = assign_parts[0].strip()
            right_side = assign_parts[-1].strip()
            if not left_side or not right_side: return False
            if left_side.startswith(("Target ", "Choose ", "Fail:", "if ", "else ", "elif ", "while ", "for ")): return False
            return True
            
        return True 

    def resolve_general_value(self, arg, allow_raw_string=False, _in_container=False):
        arg = arg.strip()
        
        if arg.startswith('work(') and arg.endswith(')'):
            inner_cmd = arg[5:-1].strip()
            if inner_cmd.startswith('working(') and inner_cmd.endswith(')'):
                work_arg = inner_cmd[8:-1].strip().strip('"')
                return self.execute_game_working_logic(work_arg)

        attr_match = re.fullmatch(r'^(\w+)\.(\w+)$', arg)
        if attr_match:
            obj_name, attr_name = attr_match.groups()
            if obj_name in self.variables and isinstance(self.variables[obj_name], dict):
                if self.variables[obj_name].get("__type__") == "instance":
                    if attr_name in self.variables[obj_name]["attrs"]:
                        return self.variables[obj_name]["attrs"][attr_name], None
                    else:
                        return None, f"Logic error: object '{obj_name}' has no property '{attr_name}'."
                elif self.variables[obj_name].get("__type__") == "game_score":
                    if attr_name == "Value":
                        return self.variables[obj_name]["attrs"]["Value"], None

        if arg.startswith("table.get(") and arg.endswith(")"):
            inner = arg[10:-1].strip()
            return self.resolve_general_value(inner, allow_raw_string=True)
        if arg.startswith("table.items(") and arg.endswith(")"):
            inner = arg[12:-1].strip()
            val, err = self.resolve_general_value(inner, allow_raw_string=True)
            if err: return None, err
            if isinstance(val, dict): return ",".join([f"{k}: {v}" for k, v in val.items()]), None
            return None, "Logic error: items() method is only valid for dict."
        if arg.startswith("table.keys(") and arg.endswith(")"):
            inner = arg[11:-1].strip()
            val, err = self.resolve_general_value(inner, allow_raw_string=True)
            if err: return None, err
            if isinstance(val, dict): return ",".join([str(k) for k in val.keys()]), None
            return None, "Logic error: keys() method is only valid for dict."
        if arg.startswith("table.values(") and arg.endswith(")"):
            inner = arg[13:-1].strip()
            val, err = self.resolve_general_value(inner, allow_raw_string=True)
            if err: return None, err
            if isinstance(val, dict): return ",".join([str(v) for v in val.values()]), None
            return None, "Logic error: values() method is only valid for dict."

        if arg.startswith("'") and arg.endswith("'"):
            return None, 'Statistics error: text must always be written with double quotes ("), using single quotes (\') is forbidden!'
        
        builtin_check, builtin_err = self.parse_builtin_functions(arg)
        if builtin_err != "Not a builtin function": return builtin_check, builtin_err

        call_match = re.match(r'^(\*?[\w.]+)\((.*)\)$', arg)
        if call_match:
            base_name = call_match.group(1).split(".")[0]
            if base_name in self.functions or base_name in self.classes:
                return self.execute_possibly_chained_call(arg, 0)

        if re.fullmatch(r'\("[^"\\]*(?:\\.[^"\\]*)*"\)', arg):
            inner = arg[2:-2]
            if allow_raw_string and not _in_container:
                return None, "Statistics error: an extra parenthesis is forbidden for text inside this function!"
            return inner, None
            
        if re.fullmatch(r'"[^"\\]*(?:\\.[^"\\]*)*"', arg):
            if not allow_raw_string:
                return None, 'Statistics error: text (string) format is wrong! Normal text must always be wrapped in quotes matching parentheses: s = ("salam")'
            return arg[1:-1], None

        _QSTR_CHAIN = r'"[^"\\]*(?:\\.[^"\\]*)*"(?:\s*\+\s*"[^"\\]*(?:\\.[^"\\]*)*")+'
        if arg.startswith('"') and arg.endswith('"') and not re.fullmatch(_QSTR_CHAIN, arg):
            if allow_raw_string:
                return arg[1:-1], None
        
        if (arg.startswith('(') and arg.endswith(')')) or (arg.startswith('[') and arg.endswith(']')):
            is_explicit_list = arg.startswith('[')
            is_explicit_tuple = arg.startswith('(')
            inner = arg[1:-1].strip()
            if not inner:
                if is_explicit_tuple: return (), None
                return [], None
                
            sub_args, unbalanced = self.smart_split(inner)
            if unbalanced:
                return None, "Statistics error: parentheses are unbalanced or the value is incomplete (e.g. an extra comma) has been entered."

            if len(sub_args) > 1 or is_explicit_list or is_explicit_tuple:
                resolved_list = []
                for sa in sub_args:
                    sa = sa.strip()
                    if not sa: continue
                    s_val, s_err = self.resolve_general_value(sa, allow_raw_string=(is_explicit_list or is_explicit_tuple), _in_container=(is_explicit_list or is_explicit_tuple))
                    if s_err: return None, s_err
                    resolved_list.append(s_val)
                if is_explicit_tuple: return tuple(resolved_list), None
                return resolved_list, None
            elif len(sub_args) == 1:
                if sub_args[0] != arg:
                    if sub_args[0].startswith('(') and sub_args[0].endswith(')') and not _in_container:
                        return None, "Statistics error: unnecessary or repeated parenthesis detected!"
                    return self.resolve_general_value(sub_args[0], allow_raw_string=allow_raw_string, _in_container=_in_container)

        if arg == "True": return True, None
        if arg == "False": return False, None
        if arg == "None": return None, None
        
        if arg in ["true", "false", "none"]:
            if arg in self.variables: return self.variables[arg], None
            return None, f"Statistics error: '{arg}' cannot be counted as bool function! Correct usage: {arg.capitalize()}"

        idx_arg = arg
        used_table_prefix = arg.startswith("table.")
        if used_table_prefix: idx_arg = arg[6:]
        idx_match = re.fullmatch(r'^([\w.]+)\[(.*)\]$', idx_arg)
        if idx_match:
            l_name = idx_match.group(1)
            idx_str = idx_match.group(2).strip()
            if idx_str in ['"end"', '"begin"', 'end', 'begin', "'end'", "'begin'"]: 
                return None, "Statistics error: \"end\" or \"begin\" cannot be an index!"
            
            obj, err = self.resolve_general_value(l_name, allow_raw_string=True)
            if not err and obj is not None:
                if isinstance(obj, list):
                    if re.fullmatch(r'\s*-?\d+\s*=\s*-?\d+(?:\s*=\s*-?\d+)*\s*', idx_str):
                        picks = [int(p.strip()) for p in idx_str.split("=")]
                        result = []
                        for p in picks:
                            if not (0 <= p < len(obj) or -len(obj) <= p < 0):
                                return None, f"Logic error: List index does not exist: '{p}'"
                            result.append(obj[p] if used_table_prefix else (p % len(obj), obj[p]))
                        return IlusSelection(result), None
                    if re.fullmatch(r'\s*-?\d+\s*,\s*-?\d+\s*', idx_str):
                        a_str, b_str = [p.strip() for p in idx_str.split(",")]
                        a_val, b_val = int(a_str), int(b_str)
                        if not (0 <= a_val < len(obj) or -len(obj) <= a_val < 0):
                            return None, f"Logic error: List index does not exist: '{a_val}'"
                        if not (0 <= b_val < len(obj) or -len(obj) <= b_val < 0):
                            return None, f"Logic error: List index does not exist: '{b_val}'"
                        a_norm, b_norm = a_val % len(obj), b_val % len(obj)
                        idx_range = range(a_norm, b_norm + 1) if a_norm <= b_norm else range(b_norm, a_norm + 1)
                        picked = [(i, obj[i]) for i in idx_range]
                        if a_norm > b_norm: picked.reverse()
                        if used_table_prefix:
                            return IlusSelection([v for _, v in picked]), None
                        return IlusSelection(picked), None
                    idx_val, err_i = self.resolve_general_value(idx_str, allow_raw_string=True)
                    if err_i: return None, err_i
                    if isinstance(idx_val, (int, float)): idx_val = int(idx_val)
                    elif isinstance(idx_val, str) and idx_val.lstrip('-').isdigit(): idx_val = int(idx_val)
                    else: return None, f"Logic error: List index must be an integer: '{idx_str}'"
                    
                    if 0 <= idx_val < len(obj) or -len(obj) <= idx_val < 0:
                        if used_table_prefix:
                            return obj[idx_val], None
                        return (idx_val % len(obj), obj[idx_val]), None
                    else:
                        return None, f"Logic error: List index does not exist: '{idx_arg}'"
                elif isinstance(obj, dict):
                    if obj.get("__type__") == "instance":
                        cls_name = obj["__class__"]
                        return None, f"Logic error: class '{cls_name}' does not support indexing."
                    else:
                        idx_val, err_i = self.resolve_general_value(idx_str, allow_raw_string=True)
                        if err_i: return None, err_i
                        if idx_val in obj: return obj[idx_val], None
                        else: return None, f"Logic error: no such key in the list: '{idx_val}'"

        val, err = self.parse_inline_types(arg)
        if err != "Not a type function": return val, err

        method_call_match = re.fullmatch(r'(\*?[\w]+\.[\w]+)\((.*)\)', arg)
        if method_call_match:
            mc_fn_name = method_call_match.group(1)
            mc_arg_str = method_call_match.group(2)
            mc_val, mc_err = self.execute_function_call(mc_fn_name, mc_arg_str, 0)
            if not mc_err: return mc_val, None
            return None, mc_err

        if arg in self.variables: return self.variables[arg], None

        if arg.lstrip('-').isdigit():
            if '.' in arg: return float(arg), None
            return int(arg), None
            
        evaluator = SafeMathEvaluator(self.variables, self)
        res = evaluator.evaluate(arg)
        if not evaluator.error: return res, None
        return None, evaluator.error

    def evaluate_repeat(self, repeat_str):
        match = re.search(r'^Repeat:\s*\((.*)\)$', repeat_str)
        if not match: return None, "Statistics error: Repeat format is wrong."
        val_str = match.group(1).strip()
        
        if val_str in ["true", "false", "none"]:
            if val_str not in self.variables:
                return None, f"Statistics error: loop condition '{val_str}' cannot be counted as bool function! Correct usage: {val_str.capitalize()}"
            
        if any(op in val_str for op in ["==", "!=", "<=", ">=", "<", ">"]) or val_str in ["True", "False"]: return None, None
            
        if "," in val_str:
            parts = val_str.split(",")
            if len(parts) not in (2, 3): return None, "Statistics error: range requires exactly 2 or 3 values."
                
            start_val, err_s = self.resolve_general_value(parts[0], allow_raw_string=True)
            end_val, err_e = self.resolve_general_value(parts[1], allow_raw_string=True)
            if err_s or err_e: return None, "Logic error: range values are errored."
            
            step = 1
            if len(parts) == 3:
                step_val, err_step = self.resolve_general_value(parts[2], allow_raw_string=True)
                if err_step: return None, "Logic error: step value is errored."
                if isinstance(step_val, (int, float)): step = int(step_val)
                elif isinstance(step_val, str) and step_val.lstrip('-').isdigit(): step = int(step_val)
                else: return None, "Logic error: step must be an integer."
                
            if (isinstance(start_val, (int, float)) or (isinstance(start_val, str) and start_val.lstrip('-').isdigit())) and \
               (isinstance(end_val, (int, float)) or (isinstance(end_val, str) and end_val.lstrip('-').isdigit())):
                s, e = int(start_val), int(end_val)
            else:
                return None, "Logic error: range values must be integers."
            
            if step == 0: return None, "Logic error: step cannot be zero."
                
            if step > 0: return list(range(s, e + 1, step)), None
            else: return list(range(e, s - 1, step)), None
            
        times_val, err = self.resolve_general_value(val_str, allow_raw_string=True)
        if err: return None, err
        if isinstance(times_val, (int, float)): times = int(times_val)
        elif isinstance(times_val, str) and times_val.lstrip('-').isdigit(): times = int(times_val)
        else: return None, "Logic error: repeat count must be an integer."
        
        if times <= 0: return None, None
        last_log = self.logs[-1] if self.logs else ""
        return [last_log] * (times - 1), None

    PARENT_PREFIXABLE_COMMANDS = ("Create:", "Size:", "Color:", "Text.Color:", "Text.Size:", "Text:",
                                   "Set:", "Score.Size:", "Score.Color:", "Score:", "Anchored:",
                                   "Transparency:", "Collision:")

    def try_parent_prefixed_command(self, line, line_num):
        """If line looks like '<name>.<Command>: ...' (e.g. Myblock.Size: 50_50), handle it as a
        parent-scoped game command. Returns (handled, error) -- handled=False means the caller
        should fall through to normal dispatch."""
        m = re.match(r'^([A-Za-z_]\w*)\.((?:Text\.Color|Text\.Size|Score\.Size|Score\.Color|Create|Size|Color|Text|Set|Score|Anchored|Transparency|Collision):.*)$', line)
        if not m:
            return False, None
        parent_name, rest = m.group(1), m.group(2)
        cmd_prefix = rest.split(":", 1)[0] + ":"
        if cmd_prefix not in self.PARENT_PREFIXABLE_COMMANDS:
            return False, None

        if cmd_prefix == "Create:":
            err = self.execute_game_command(rest, line_num)
            if err: return True, err
            self.named_objects[parent_name] = self.current_obj
            self.variables[parent_name] = self.current_obj
            return True, None

        target_obj = self.named_objects.get(parent_name)
        if target_obj is None:
            return True, f"Logic error: parent '{parent_name}' not found. Use '{parent_name}.Create: \"...\"' first."
        prev_current = self.current_obj
        self.current_obj = target_obj
        err = self.execute_game_command(rest, line_num)
        self.current_obj = prev_current
        return True, err

    def execute_game_command(self, line, line_num):
        if not getattr(self, 'ilusaztar_imported', False):
            return "The 'ilusaztar' module must be imported for game functions!"

        parent_match = re.match(r'^(\w+)\.(Create:|Size:|Color:|Text:|Shape:|Text\.Color:|Text\.Size:|Set:|Score:|Score\.Color:|Score\.Size:|Anchored:|Collision:|Transparency:)', line)
        if parent_match and parent_match.group(1) not in ("Text", "Score"):
            parent_name, rest = parent_match.group(1), line[len(parent_match.group(1)) + 1:]
            if rest.startswith("Create:"):
                err = self.execute_game_command(rest, line_num)
                if err: return err
                self.named_objects[parent_name] = self.current_obj
                if self.current_obj.get("identity") is None:
                    self.current_obj["identity"] = parent_name
                return None
            else:
                if parent_name not in self.named_objects:
                    if rest.startswith("Text:"):
                        if not self.game_slot_active: return "Please start the game slot first."
                        new_text_obj = {"type": "UI", "size": (40, 40), "color": "black", "x": 175, "y": 20,
                                        "identity": parent_name, "text": None, "text_color": None, "text_size": None,
                                        "gravity": False, "anchored": True, "can_collide": False,
                                        "transparency": 0.0, "move_dx": 5, "move_dy": 5, "text_only": True}
                        self.game_objects.append(new_text_obj)
                        self.named_objects[parent_name] = new_text_obj
                        self.variables[parent_name] = new_text_obj
                        self.current_obj = new_text_obj
                        return self.execute_game_command(rest, line_num)
                    return f"Logic error: '{parent_name}' has no created object (use {parent_name}.Create: first)."
                self.current_obj = self.named_objects[parent_name]
                return self.execute_game_command(rest, line_num)

        if line.startswith("sup_devices("):
            if not self.game_slot_active: return "Please start the game slot first."
            inner = line[len("sup_devices("):-1] if line.endswith(")") else None
            if inner is None: return 'sup_devices() format is wrong. Format: sup_devices("M"=True,"P"=True)'
            parts, unbal = self.smart_split(inner)
            if unbal: return "sup_devices() arguments are unbalanced."
            devices = {}
            for p in parts:
                kv = re.fullmatch(r'\s*"([MP])"\s*=\s*(True|False)\s*', p)
                if not kv: return f"sup_devices() format is wrong near '{p}'. Use \"M\"/\"P\" = True/False."
                devices[kv.group(1)] = (kv.group(2) == "True")
            self.supported_devices.update(devices)
            self.log(f"Supported devices set: {self.supported_devices}")
            return None

        if line == "End()":
            if not self.game_slot_active: return "Please start the game slot first."
            self.should_end_game = True
            self.log("Game window closed.")
            return None

        if line.startswith("Score:"):
            if not self.game_slot_active: return "Please start the game slot first."
            match = re.fullmatch(r'Score:\s*\((#+)\)', line)
            if not match: return "Score format is wrong. Example: Score: (###)"
            hashes = match.group(1)
            max_digits = len(hashes)
            max_val = (10 ** max_digits) - 1
            
            score_obj = {
                "type": "score",
                "max_val": max_val,
                "value": 0,
                "x": 10,
                "y": 10, 
                "text_color": "black",
                "text_size": (16, 16)
            }
            self.game_objects.append(score_obj)
            self.variables["Score"] = {"__type__": "game_score", "obj": score_obj, "attrs": {"Value": 0}}
            self.current_obj = score_obj
            self.log(f"Score scale created: Maximum {max_val}")
        
        elif line.startswith("Score.Color:"):
            color_part = line.split("Score.Color:")[1].strip()
            if color_part.startswith('"') and color_part.endswith('"'): color_val = color_part[1:-1]
            elif color_part.startswith('("') and color_part.endswith('")'): color_val = color_part[2:-2]
            elif color_part.startswith("'") and color_part.endswith("'"): return "Statistics error: use double quotes!"
            else:
                color_val, err = self.resolve_general_value(color_part, allow_raw_string=False)
                if err: return err
            
            if str(color_val).lower() not in VALID_COLORS: return f"'{color_val}' is an unrecognized color."
            if "Score" in self.variables:
                self.variables["Score"]["obj"]["text_color"] = str(color_val).lower()
            self.log(f"Score scale color set: {color_val}")
            
        elif line.startswith("Score.Size:"):
            size_part = line.split("Score.Size:")[1].strip()
            if "_" in size_part:
                w_str, h_str = [s.strip() for s in size_part.split("_")]
                w_val, err_w = self.resolve_general_value(w_str, allow_raw_string=True)
                h_val, err_h = self.resolve_general_value(h_str, allow_raw_string=True)
                if not err_w and not err_h:
                    if "Score" in self.variables: self.variables["Score"]["obj"]["text_size"] = (int(w_val), int(h_val))
            else:
                s_val, err = self.resolve_general_value(size_part, allow_raw_string=True)
                if not err:
                    if "Score" in self.variables: self.variables["Score"]["obj"]["text_size"] = (int(s_val), int(s_val))
            self.log(f"Score scale size set.")

        elif line.startswith("Create:"):
            if not self.game_slot_active: return "Please start the game slot first."
            match = re.fullmatch(r'Create:\s*"(block|circle|UI|area|GUI)"', line)
            if not match: return f"'{line}' format is wrong."
            obj_type = match.group(1)
            if obj_type == "GUI": obj_type = "UI"
            anchored_def = False if obj_type in ["block", "circle"] else True
            self.current_obj = {"type": obj_type, "size": (40, 40), "color": "black", "x": 175, "y": 20, "identity": None, "text": None, "text_color": None, "text_size": None, "gravity": False, "anchored": anchored_def, "can_collide": True, "transparency": 0.0, "move_dx": 5, "move_dy": 5}
            self.game_objects.append(self.current_obj)
            self.log(f"Object: {obj_type} created.")
            
        elif line.startswith("Set:"):
            if not self.current_obj: return "'Create' is required first for identity."
            # Fix + New feature: Custom entity IDs support
            # Format 1: Set: "entity" (orijinal)
            # Format 2: Set: "entity" = user.id = 1918 (custom ID)
            # Format 3: Set: "entity" = user.id = int (auto-generated ID)
            if "=" in line:
                parts = line.split("=")
                if len(parts) >= 3:
                    identity_part = parts[0].split("Set:")[1].strip().strip('"').strip("'")
                    custom_id_str = parts[-1].strip()
                    if custom_id_str == "int":
                        self._next_entity_id = getattr(self, "_next_entity_id", 0) + 1
                        custom_id = self._next_entity_id
                    else:
                        custom_id = int(custom_id_str)
                    self.current_obj["identity"] = identity_part
                    self.current_obj["custom_id"] = custom_id
                    self.log(f"Identity '{identity_part}' and custom ID '{custom_id}' set.")
                else:
                    return "Custom ID format is wrong. Format: Set: \"entity\" = user.id = 1918"
            else:
                # Orijinal format
                match = re.fullmatch(r'Set:\s*"(entity|object)"', line)
                if not match: return "Identity format is wrong."
                self.current_obj["identity"] = match.group(1)
                self.log(f"Identity: {match.group(1)} set.")

        elif line.startswith("move:"):
            if not getattr(self, 'ilusaztar_imported', False):
                return "The 'ilusaztar' package must be installed for game functions!"
            _, err = self.execute_game_working_logic(line)
            return err

        elif line.startswith("Anchored:"):
            if not self.current_obj: return "'Create' is required for the object."
            val_str = line.split("Anchored:")[1].strip()
            val, err = self.resolve_general_value(val_str, allow_raw_string=False)
            if err: return err
            is_valid, verr = PropertyValidator.validate_property("anchored", val)
            if not is_valid: return verr
            self.current_obj["anchored"] = val
            self.log(f"Anchored set: {val}")

        elif line.startswith("Collision:"):
            if not self.current_obj: return "'Create' is required for the object."
            val_str = line.split("Collision:")[1].strip()
            val, err = self.resolve_general_value(val_str, allow_raw_string=False)
            if err: return err
            is_valid, verr = PropertyValidator.validate_property("collision", val)
            if not is_valid: return verr
            self.current_obj["can_collide"] = val
            self.log(f"Collision set: {val}")

        elif line.startswith("Transparency:"):
            if not self.current_obj: return "'Create' is required for the object."
            val_str = line.split(":", 1)[1].strip()
            val, err = self.resolve_general_value(val_str, allow_raw_string=False)
            if err: return err
            is_valid, verr = PropertyValidator.validate_property("transparency", val)
            if not is_valid: return verr
            self.current_obj["transparency"] = float(val)
            self.log(f"Transparency set: {self.current_obj['transparency']}")
            
        elif line.startswith("Size:"):
            if not self.current_obj: return "'Create' is required for size."
            size_part = line.split("Size:")[1].strip()
            if "_" not in size_part: return "Size format must be 'Width_Height'."
            w_str, h_str = [s.strip() for s in size_part.split("_")]
            w_val, err_w = self.resolve_general_value(w_str, allow_raw_string=True)
            h_val, err_h = self.resolve_general_value(h_str, allow_raw_string=True)
            if err_w or err_h or not str(w_val).lstrip('-').isdigit() or not str(h_val).lstrip('-').isdigit():
                return "Size values are not valid numbers."
            self.current_obj["size"] = (int(w_val), int(h_val))
            self.log(f"Size set: {w_val}x{h_val}")
            
        elif line.startswith("Shape:"):
            if not self.current_obj: return "'Create' is required for shape."
            shape_part = line.split("Shape:")[1].strip()
            if shape_part.startswith('"') and shape_part.endswith('"'): shape_val = shape_part[1:-1]
            elif shape_part.startswith('("') and shape_part.endswith('")'): shape_val = shape_part[2:-2]
            elif shape_part.startswith("'") and shape_part.endswith("'"): return "Statistics error: use double quotes!"
            else:
                shape_val, err = self.resolve_general_value(shape_part, allow_raw_string=False)
                if err: return err
            shape_val = str(shape_val).lower()
            if shape_val not in ("block", "circle"): return f"'{shape_val}' is not a supported shape. Use \"block\" or \"circle\"."
            self.current_obj["type"] = shape_val
            self.log(f"Shape set: {shape_val}")

        elif line.startswith("Color:"):
            if not self.current_obj: return "'Create' is required for color."
            color_part = line.split("Color:")[1].strip()
            if color_part.startswith('"') and color_part.endswith('"'): color_val = color_part[1:-1]
            elif color_part.startswith('("') and color_part.endswith('")'): color_val = color_part[2:-2]
            elif color_part.startswith("'") and color_part.endswith("'"): return "Statistics error: use double quotes!"
            else:
                color_val, err = self.resolve_general_value(color_part, allow_raw_string=False)
                if err: return err
            if str(color_val).lower() == "unknown":
                self.current_obj["color"] = "gray"
                self.current_obj["can_collide"] = False
                self.log("Color set: unknown (object made non-collidable)")
            else:
                if str(color_val).lower() not in VALID_COLORS: return f"'{color_val}' is an unrecognized color."
                self.current_obj["color"] = str(color_val).lower()
                self.log(f"Color set: {color_val}")
            
        elif line.startswith("Text.Color:"):
            if not self.current_obj: return "'Create' is required first for text color."
            color_part = line.split("Text.Color:")[1].strip()
            if color_part.startswith('("') and color_part.endswith('")'): color_val = color_part[2:-2]
            elif color_part.startswith("'") and color_part.endswith("'"): return "Statistics error: use double quotes!"
            elif color_part.startswith('"') and color_part.endswith('"'): color_val = color_part[1:-1]
            else:
                color_val, err = self.resolve_general_value(color_part, allow_raw_string=False)
                if err: return err
            if str(color_val).lower() not in VALID_COLORS: return f"'{color_val}' is an unrecognized color."
            self.current_obj["text_color"] = str(color_val).lower()
            self.log(f"Text color set: {color_val}")
            
        elif line.startswith("Text.Size:"):
            if not self.current_obj: return "'Create' is required first for text size."
            size_part = line.split("Text.Size:")[1].strip()
            if "_" in size_part:
                w_str, h_str = [s.strip() for s in size_part.split("_")]
                w_val, err_w = self.resolve_general_value(w_str, allow_raw_string=True)
                h_val, err_h = self.resolve_general_value(h_str, allow_raw_string=True)
                if err_w or err_h or not str(w_val).lstrip('-').isdigit() or not str(h_val).lstrip('-').isdigit():
                    return "Text size values are not valid numbers."
                self.current_obj["text_size"] = (int(w_val), int(h_val))
                self.log(f"Text size set: {w_val}x{h_val}")
            else:
                s_val, err_s = self.resolve_general_value(size_part, allow_raw_string=True)
                if err_s or not str(s_val).lstrip('-').isdigit():
                    return "Text size values are not valid numbers."
                self.current_obj["text_size"] = (int(s_val), int(s_val))
                self.log(f"Text size set: {s_val}x{s_val}")
            
        elif line.startswith("Text:"):
            if not self.current_obj:
                if not self.game_slot_active: return "'Create' is required first for text."
                self.current_obj = {"type": "UI", "size": (40, 40), "color": "black", "x": 175, "y": 20,
                                     "identity": None, "text": None, "text_color": None, "text_size": None,
                                     "gravity": False, "anchored": True, "can_collide": False,
                                     "transparency": 0.0, "move_dx": 5, "move_dy": 5, "text_only": True}
                self.game_objects.append(self.current_obj)
            text_part = line.split("Text:")[1].strip()
            if text_part.startswith('("') and text_part.endswith('")'): text_val = text_part[2:-2]
            elif text_part.startswith("'") and text_part.endswith("'"): return "Statistics error: use double quotes!"
            elif text_part.startswith('"') and text_part.endswith('"'):
                return 'Statistics error: Text: syntax requires parentheses. Correct usage: Text: ("...")'
            else:
                text_val, err = self.resolve_general_value(text_part, allow_raw_string=False)
                if err: return err
            self.current_obj["text"] = str(text_val)
            self.log(f"Text added: {text_val}")
            
        elif line.startswith("UI("):
            if not self.game_slot_active: return "Please start the game slot first."
            match = re.fullmatch(r'UI\((left|right|jump|up|down)(?:\s*,\s*Bind\s*=\s*([A-Za-z_]\w*))?\)', line)
            if not match: return "UI() format is wrong. Supported: left, right, jump, up, down."
            action = match.group(1)
            bound_fn = match.group(2)

            if bound_fn:
                if bound_fn not in self.functions:
                    return f"Logic error: function '{bound_fn}' bound with Bind was not found. Define it before using UI(...)."
                def_line = self.functions[bound_fn].get("def_line")
                if def_line is not None and def_line > line_num:
                    return f"Statistics error: function '{bound_fn}' must be defined before it is used in Bind (interpreter reads line by line)."
                fn_param_names = [p["name"] for p in self.functions[bound_fn].get("params", [])]
                if "event" not in fn_param_names:
                    return f"Statistics error: the function bound with Bind ('{bound_fn}') must have a parameter named 'event'. Example: function {bound_fn}(event):"

            if self.current_obj and self.current_obj["type"] == "UI":
                if not self.supported_devices.get("M", True):
                    return "Logic error: mobile ('M') device support is disabled via sup_devices() - cannot create a mobile UI button."
                self.current_obj["ui_action"] = action
                if bound_fn:
                    self.current_obj["ui_bound_event"] = bound_fn
                self.log(f"UI Mobile button set: {action}")
            else:
                if not self.supported_devices.get("P", True):
                    return "Logic error: PC ('P') device support is disabled via sup_devices() - cannot register a keyboard control."
                if action not in self.pc_controls:
                    self.pc_controls.append(action)
                if bound_fn:
                    self.pc_control_binds[action] = bound_fn
                self.log(f"PC keyboard control activated: {action}")
        
        # New feature #2a: weld() function - binds two objects to each other
        elif line.startswith("weld"):
            match = re.fullmatch(r'weld\((.*?),(.*?)\)', line)
            if not match: return "weld() format is wrong. Format: weld(obj1, obj2)"
            obj1_name = match.group(1).strip()
            obj2_name = match.group(2).strip()
            
            obj1, err1 = self.resolve_general_value(obj1_name, allow_raw_string=False)
            obj2, err2 = self.resolve_general_value(obj2_name, allow_raw_string=False)
            
            if err1 or err2: return f"weld() invalid objects: {err1 or err2}"
            
            # Bind game objects
            if isinstance(obj1, dict) and "type" in obj1 and isinstance(obj2, dict) and "type" in obj2:
                # keep bound objects in sync
                if "welded_to" not in obj1: obj1["welded_to"] = []
                if "welded_to" not in obj2: obj2["welded_to"] = []
                obj1["welded_to"].append(obj2)
                obj2["welded_to"].append(obj1)
                self.log(f"Objects '{obj1_name}' and '{obj2_name}' welded to each other.")
            else:
                return "weld() can only be used with game objects."
        
        # New feature #4: GUI support - set GUI buttons and text
        elif line.startswith("GUI"):
            if not self.game_slot_active: return "Please start the game slot first."
            match = re.fullmatch(r'GUI\((Button|Text|Label|InputBox|Input|GUI|Image)(.*)\)', line)
            if not match: return "GUI() format is wrong. Format: GUI(Button, Text=\"...\", Bind=event_name)"
            
            gui_type = match.group(1)
            params_str = match.group(2).strip().strip("(),")
            
            if not self.current_obj: 
                return "Create: \"UI\" is required first for GUI."
            
            # First save the GUI type and its parameters
            self.current_obj["gui_type"] = gui_type
            self.current_obj["gui_bound_event"] = None

            if gui_type == "Image":
                # GUI(Image, "photo.png") - second argument is the image file name (with extension).
                img_match = re.search(r'"([^"]+)"', params_str)
                if not img_match:
                    return 'GUI(Image, ...) format is wrong. Format: GUI(Image, "filename.png")'
                self.current_obj["image_path"] = img_match.group(1)
                # Strip the bare filename token out so the Key=Value parser below doesn't trip on it.
                params_str = (params_str[:img_match.start()] + params_str[img_match.end():]).strip(", ").strip()
            
            # Parse parameters (Text=..., Bind=..., Size=..., Color=...)
            if re.search(r'\w+\s*=\s*(?:,|$)', params_str):
                return "GUI() format is wrong. Every parameter needs a non-empty value, e.g. Bind=funcName (not Bind=)."
            params = re.findall(r'(\w+)\s*=\s*([^,]+(?:,[^=]*)?)', params_str)
            for param_name, param_val in params:
                param_val = param_val.strip()
                if param_name == "Text":
                    param_val = param_val.strip('"').strip("'")
                    self.current_obj["text"] = param_val
                elif param_name == "Bind":
                    # Save the event name - must be a function name
                    bound_fn = param_val.strip('"').strip("'")
                    if bound_fn not in self.functions:
                        return f"Logic error: function '{bound_fn}' bound with Bind was not found. Define it before using GUI(...)."
                    def_line = self.functions[bound_fn].get("def_line")
                    if def_line is not None and def_line > line_num:
                        return f"Statistics error: function '{bound_fn}' must be defined before it is used in Bind (interpreter reads line by line)."
                    fn_param_names = [p["name"] for p in self.functions[bound_fn].get("params", [])]
                    if "event" not in fn_param_names:
                        return f"Statistics error: the function bound with Bind ('{bound_fn}') must have a parameter named 'event'. Example: function {bound_fn}(event):"
                    self.current_obj["gui_bound_event"] = bound_fn
                elif param_name == "Size":
                    if "_" in param_val:
                        parts = param_val.split("_")
                        w, h = int(parts[0]), int(parts[1])
                        self.current_obj["size"] = (w, h)
                elif param_name == "Color":
                    param_val = param_val.strip('"').strip("'")
                    self.current_obj["color"] = param_val
            
            if gui_type == "Image":
                self.log(f"GUI Image set: {self.current_obj['image_path']}")
            else:
                self.log(f"GUI {gui_type} created")
                
        return None

    def handle_del(self, line, line_num):
        target_str = line[4:].strip()
        
        clean_target = target_str
        if target_str.startswith("table."):
            clean_target = target_str[6:]
        
        idx_match = re.fullmatch(r'^([\w.]+)\[(.*)\]$', clean_target)
        if idx_match:
            l_name = idx_match.group(1)
            idx_str = idx_match.group(2).strip()
            
            obj, err = self.resolve_general_value(l_name, allow_raw_string=True)
            if err: return err
            if obj is None: return f"Logic error: '{l_name}' not found."
            
            if isinstance(obj, list):
                idx_val, err_i = self.resolve_general_value(idx_str, allow_raw_string=True)
                if err_i: return err_i
                if isinstance(idx_val, (int, float)): idx_val = int(idx_val)
                elif isinstance(idx_val, str) and idx_val.lstrip('-').isdigit(): idx_val = int(idx_val)
                else: return f"Logic error: Index must be an integer."
                
                if 0 <= idx_val < len(obj) or -len(obj) <= idx_val < 0:
                    del obj[idx_val]
                else:
                    return f"Logic error: Index not found."
            elif isinstance(obj, dict) and obj.get("__type__") == "instance":
                cls_name = obj["__class__"]
                if "__calldelitem__" in self.classes[cls_name]["methods"]:
                    idx_val, err_i = self.resolve_general_value(idx_str, allow_raw_string=True)
                    if err_i: return err_i
                    _, m_err = self.execute_method_with_values(obj, cls_name, "__calldelitem__", [idx_val])
                    if m_err: return m_err
                else:
                    return f"Logic error: class '{cls_name}' has no '__calldelitem__' method."
            elif isinstance(obj, dict) and obj.get("__type__") not in ["instance", "file", "game_score"]:
                idx_val, err_i = self.resolve_general_value(idx_str, allow_raw_string=True)
                if err_i: return err_i
                if idx_val in obj:
                    del obj[idx_val]
                else:
                    return f"Logic error: Key not found."
            else:
                return f"Logic error: '{l_name}' is not a deletable indexed object."
            gc.collect()
            return None
            
        if not re.fullmatch(r'^\*?\w+$', target_str):
            val, err = self.resolve_general_value(target_str, allow_raw_string=True)
            if not err:
                self.log(str(val))
                return None
            return f"Logic error: '{target_str}' is not a valid variable name and cannot be deleted."
            
        if target_str in self.variables:
            del_val = self.variables[target_str]
            if isinstance(del_val, dict) and del_val.get("__type__") == "instance":
                cls_name = del_val["__class__"]
                if "__calldelitem__" in self.classes[cls_name]["methods"]:
                    _, m_err = self.execute_method_with_values(del_val, cls_name, "__calldelitem__", [])
                    if m_err: return m_err
            del self.variables[target_str]
            if target_str in self.variable_origins:
                del self.variable_origins[target_str]
        else:
            return f"Logic error: name '{target_str}' - Variable not found."
        gc.collect()
        return None

    def execute_statement_by_string(self, line, line_num):
        line = line.strip()
        if not line or line.startswith("return ") or line == "return": return None
        
        from_import_match = re.match(r'^from\s+([\w.]+)\s+import\s+(.+)$', line)
        if from_import_match:
            return self.handle_from_import(from_import_match.group(1).strip(), from_import_match.group(2).strip(), line_num)
        if line.startswith("import "): return self.handle_import_multi(line[7:].strip(), line_num)
        if line.startswith("wait(") and line.endswith(")"):
            if not getattr(self, 'ilusaztar_imported', False):
                return "The 'ilusaztar' package must be installed for game functions!"
            val_str = line[5:-1].strip()
            val, err = self.resolve_general_value(val_str, allow_raw_string=True)
            if err: return err
            if isinstance(val, (int, float)):
                time.sleep(val)
                return None
            return "Error: wait() only accepts a number."
        
        if line == "pass": return None
        if line == "break": return "break"
        if line.startswith("break "):
            func_target = line[6:].strip()
            self.log(f"System: {func_target} stopped.")
            return "break"
        if line == "continue":
            self.log("System: Continuing...")
            return "continue"
        if line.startswith("del "):
            err = self.handle_del(line, line_num)
            return err
            
        fn_match = re.match(r'^(\*?[\w.]+)\((.*)\)$', line)
        if fn_match and fn_match.group(1) not in ["write", "work", "UI", "wait"]:
            fn_name = fn_match.group(1)
            arg_str = fn_match.group(2)
            if fn_name in ["free", "abs", "round", "open", "zip", "think", "locals", "globals", "len", "max", "min", "type", "check", "id", "int", "str", "fround", "bool", "list", "dict", "tuple", "TYPE", "setattr", "getattr"]:
                val, err = self.resolve_general_value(line, allow_raw_string=True)
                return err
            elif fn_name in self.functions or fn_name in self.classes or ("." in fn_name):
                ret_val, err = self.execute_possibly_chained_call(line, line_num)
                return err
            
        if line.startswith(("Create:", "Set:", "Size:", "Color:", "Text:", "Shape:", "Text.Color:", "Text.Size:", "UI(", "Score:", "Score.Color:", "Score.Size:", "Anchored:", "Collision:", "Transparency:", "GUI", "move:", "End(", "sup_devices(")) or re.match(r'^\w+\.(Create:|Size:|Color:|Text:|Shape:|Text\.Color:|Text\.Size:|Set:|Score:|Score\.Color:|Score\.Size:|Anchored:|Collision:|Transparency:)', line):
            err = self.execute_game_command(line, line_num)
            return err
        elif line in ["work Sx.game", "work Area.game"]:
            if not getattr(self, 'ilusaztar_imported', False): return "The 'ilusaztar' module must be imported for game functions!"
            if self.game_slot_active: return "The game slot is already active."
            self.game_slot_active = True
            self.log(f"System: {line} activated.")
            return None
        elif line.startswith("work("):
            val, err = self.handle_work_syntax(line)
            if err: return err
            if val is not None: self.log(val)
            return None
        elif line.startswith("write("):
            _, err = self.handle_write(line)
            return err
        elif re.match(r'^(free|abs|round|open|zip|think|locals|globals|len|max|min|type|check|id|int|str|fround|bool|list|dict|tuple|TYPE)\(', line):
            val, err = self.resolve_general_value(line, allow_raw_string=True)
            return err
        elif re.search(r'\s*(\+=|-=|\*\*=|\*=|//=|/=|%=|\^=)\s*', line) or "=" in line:
            err = self.handle_assignment(line, line_num)
            return err
        else:
            val, err = self.resolve_general_value(line, allow_raw_string=True)
            if not err:
                self.variables["_"] = val 
                return None
            return f"This command cannot be repeated or is unknown: '{line}'"

    def execute_function_with_values(self, fn_name, args_vals):
        fn_data = self.functions[fn_name]
        params = fn_data["params"]
        
        has_star_args = len(params) > 0 and params[-1]["name"].startswith("*")
        star_param_name = params[-1]["name"].lstrip("*") if has_star_args else None
        max_args = len(params) - (1 if has_star_args else 0)
        
        if not has_star_args and len(args_vals) > max_args:
            return None, f"Logic error: function '{fn_name}' expects at most {max_args} arguments."
            
        old_vars = {}
        vars_before = set(self.variables.keys())
        
        for p in params:
            p_name = p["name"]
            if p_name in self.variables: old_vars[p_name] = self.variables[p_name]
            
        for i in range(max_args):
            p_name = params[i]["name"]
            if i < len(args_vals):
                type_err = self._check_param_type(params[i], args_vals[i], fn_name)
                if type_err:
                    for v in set(self.variables.keys()) - vars_before: del self.variables[v]
                    for p in old_vars: self.variables[p] = old_vars[p]
                    return None, type_err
                self.variables[p_name] = args_vals[i]
            else:
                if params[i]["default"] is None:
                    for v in set(self.variables.keys()) - vars_before: del self.variables[v]
                    for p in old_vars: self.variables[p] = old_vars[p]
                    return None, f"Logic error: a value is required for parameter '{p_name}' in function '{fn_name}'."
                def_val, err = self.resolve_general_value(params[i]["default"], allow_raw_string=True)
                if err:
                    for v in set(self.variables.keys()) - vars_before: del self.variables[v]
                    for p in old_vars: self.variables[p] = old_vars[p]
                    return None, err
                self.variables[p_name] = def_val
                
        if has_star_args:
            if len(args_vals) > max_args:
                self.variables[star_param_name] = args_vals[max_args:]
            else:
                self.variables[star_param_name] = []
                
        self._scope_stack.append(vars_before)
        try:
            ret_val, err, signal = self.execute_lines(fn_data["body"])
        finally:
            self._scope_stack.pop()
        
        vars_after = set(self.variables.keys())
        added_vars = vars_after - vars_before
        for v in added_vars:
            if v in self.variables:
                del self.variables[v]
                
        for p in params:
            p_name = p["name"]
            if p_name in old_vars: self.variables[p_name] = old_vars[p_name]
            else:
                if p_name in self.variables: del self.variables[p_name]
                    
        if err: return None, err
        return ret_val, None

    def execute_method_with_values(self, instance, class_name, method_name, args_vals):
        method_data = self.classes[class_name]["methods"][method_name]
        params = method_data["params"]
        
        has_star_args = len(params) > 0 and params[-1]["name"].startswith("*")
        star_param_name = params[-1]["name"].lstrip("*") if has_star_args else None
        max_args = len(params) - (1 if has_star_args else 0)
        
        # Only bind the instance into the call when the method explicitly declares
        # a first parameter named 'self' (the convention used elsewhere in Ilus,
        # e.g. __create__(self):). A method declared without 'self' - even with
        # real parameters, like `function square_root(m):` - is treated as not
        # needing the instance at all: its declared params are real arguments,
        # not an implicit slot for the instance.
        wants_self = bool(params) and params[0]["name"] == "self"
        provided_args = ([instance] + args_vals) if wants_self else list(args_vals)
        
        if not has_star_args and len(provided_args) > max_args:
            return None, f"Logic error: method '{method_name}' expects at most {max_args} arguments."
            
        old_vars = {}
        vars_before = set(self.variables.keys())
        
        for p in params:
            p_name = p["name"]
            if p_name in self.variables: old_vars[p_name] = self.variables[p_name]
            
        for i in range(max_args):
            p_name = params[i]["name"]
            if i < len(provided_args):
                if not (wants_self and i == 0):
                    type_err = self._check_param_type(params[i], provided_args[i], method_name)
                    if type_err:
                        for v in set(self.variables.keys()) - vars_before: del self.variables[v]
                        for p in old_vars: self.variables[p] = old_vars[p]
                        return None, type_err
                self.variables[p_name] = provided_args[i]
            else:
                if params[i]["default"] is None:
                    for v in set(self.variables.keys()) - vars_before: del self.variables[v]
                    for p in old_vars: self.variables[p] = old_vars[p]
                    return None, f"Logic error: a value is required for parameter '{p_name}' in method '{method_name}'."
                def_val, err = self.resolve_general_value(params[i]["default"], allow_raw_string=True)
                if err:
                    for v in set(self.variables.keys()) - vars_before: del self.variables[v]
                    for p in old_vars: self.variables[p] = old_vars[p]
                    return None, err
                self.variables[p_name] = def_val
                
        if has_star_args:
            if len(provided_args) > max_args:
                self.variables[star_param_name] = provided_args[max_args:]
            else:
                self.variables[star_param_name] = []
                
        self._scope_stack.append(vars_before)
        try:
            ret_val, err, signal = self.execute_lines(method_data["body"])
        finally:
            self._scope_stack.pop()
        
        vars_after = set(self.variables.keys())
        added_vars = vars_after - vars_before
        for v in added_vars:
            if v in self.variables:
                del self.variables[v]
                
        for p in params:
            p_name = p["name"]
            if p_name in old_vars: self.variables[p_name] = old_vars[p_name]
            else:
                if p_name in self.variables: del self.variables[p_name]
                
        if err: return None, err
        return ret_val, None

    def split_call_chain(self, s):
        s = s.strip()
        segments = []
        i = 0
        n = len(s)
        while i < n:
            start = i
            while i < n and (s[i].isalnum() or s[i] == '_' or s[i] == '*'):
                i += 1
            if i == start: return None
            name = s[start:i]
            if i >= n or s[i] != '(': return None
            depth = 0
            j = i
            in_str = None
            while j < n:
                ch = s[j]
                if in_str:
                    if ch == in_str and s[j - 1] != '\\': in_str = None
                elif ch in ('"', "'"):
                    in_str = ch
                elif ch == '(':
                    depth += 1
                elif ch == ')':
                    depth -= 1
                    if depth == 0: break
                j += 1
            if depth != 0 or j >= n: return None
            arg_str = s[i + 1:j]
            segments.append((name, arg_str))
            i = j + 1
            if i < n:
                if s[i] == '.': i += 1
                else: return None
        if not segments: return None
        return segments

    def execute_possibly_chained_call(self, s, line_num):
        segments = self.split_call_chain(s)
        if not segments or len(segments) == 1:
            m = re.match(r'^(\*?[\w.]+)\((.*)\)$', s)
            if not m: return None, f"Statistics error (Line {line_num}): Call format is wrong."
            return self.execute_function_call(m.group(1), m.group(2), line_num)

        name0, arg0 = segments[0]
        current, err = self.execute_function_call(name0, arg0, line_num)
        if err: return None, err

        for name_i, arg_i in segments[1:]:
            if not (isinstance(current, dict) and current.get("__type__") == "instance"):
                return None, f"Logic error: previous result must be an instance for chained call '{name_i}()'."
            outer_class = current["__class__"]
            nested_full_name = f"{outer_class}.{name_i}"

            resolved_args = []
            if arg_i.strip():
                args_raw, unbal = self.smart_split(arg_i)
                if unbal: return None, f"Statistics error (Line {line_num}): parentheses are unbalanced."
                for a in args_raw:
                    v, aerr = self.resolve_general_value(a, allow_raw_string=True)
                    if aerr: return None, aerr
                    resolved_args.append(v)

            if nested_full_name in self.classes:
                class_data = self.classes[nested_full_name]
                new_instance = {"__type__": "instance", "__class__": nested_full_name, "attrs": {}, "id": id(dict())}
                if "__create__" in class_data["methods"]:
                    _, cerr = self.execute_method_with_values(new_instance, nested_full_name, "__create__", resolved_args)
                    if cerr: return None, cerr
                elif resolved_args:
                    return None, f"Logic error: class '{nested_full_name}' has no '__create__' method, but arguments were passed."
                current = new_instance
            elif name_i in self.classes[outer_class]["methods"]:
                ret, merr = self.execute_method_with_values(current, outer_class, name_i, resolved_args)
                if merr: return None, merr
                current = ret
            else:
                return None, f"Logic error: no method or nested class named '{name_i}' in class '{outer_class}'."
        return current, None

    def execute_function_call(self, fn_name, arg_str, line_num):
        try:
            args_raw, unbalanced = self.smart_split(arg_str)
            if unbalanced: return None, f"Statistics error (Line {line_num}): parentheses are unbalanced or the value is incomplete."
            
            resolved_args = []
            if len(args_raw) == 1 and not args_raw[0]: 
                pass
            else:
                for arg in args_raw:
                    # specific scenarios for Kwargs / dict pair checks
                    if arg.startswith("name="):
                        resolved_args.append({"__kwarg_name__": arg[5:].strip()})
                    elif ":" in arg and not arg.startswith("{") and not arg.startswith('"') and not arg.startswith("'"):
                        resolved_args.append({"__dict_pair__": arg.strip()})
                    else:
                        val, err = self.resolve_general_value(arg, allow_raw_string=True)
                        if err: return None, f"Statistics error (Line {line_num}): {err}"
                        resolved_args.append(val)

            if "." in fn_name and fn_name not in self.functions and fn_name not in self.classes:
                mod_prefix, suffix = fn_name.split(".", 1)
                if mod_prefix in self.imported_modules and "." not in suffix and (suffix in self.classes or suffix in self.functions):
                    fn_name = suffix

            if fn_name in self.classes:
                class_data = self.classes[fn_name]
                new_instance = {"__type__": "instance", "__class__": fn_name, "attrs": {}, "id": id(dict())}
                
                if "__create__" in class_data["methods"]:
                    _, err = self.execute_method_with_values(new_instance, fn_name, "__create__", resolved_args)
                    if err: return None, err
                elif resolved_args:
                    return None, f"Logic error: class '{fn_name}' has no '__create__' method, but arguments were passed."
                    
                return new_instance, None

            if fn_name in self.functions:
                fn_data = self.functions[fn_name]
                if fn_data.get("__type__") == "py_function":
                    try:
                        res = fn_data["func"](*resolved_args)
                        return res, None
                    except Exception as e:
                        return None, f"Python function error: {str(e)}"
                return self.execute_function_with_values(fn_name, resolved_args)

            if "." in fn_name:
                obj_name, method_name = fn_name.split(".", 1)
                
                if method_name.startswith("__") and method_name.endswith("__"):
                    return None, f"Logic error: '{method_name}' is a Treasure (hidden) method and cannot be called directly!"
                
                actual_obj_name = obj_name[1:] if obj_name.startswith("*") else obj_name

                if actual_obj_name in self.variables:
                    obj = self.variables[actual_obj_name]
                    
                    if isinstance(obj, dict):
                        if obj.get("__type__") == "instance":
                            if method_name in self.classes[obj["__class__"]]["methods"]:
                                return self.execute_method_with_values(obj, obj["__class__"], method_name, resolved_args)
                            return None, f"Logic error: class '{obj['__class__']}' has no method '{method_name}'."
                        elif obj.get("__type__") == "file":
                            if method_name == "close":
                                if hasattr(obj["handle"], "close"):
                                    obj["handle"].close()
                                    return None, None
                                else:
                                    return None, "File error: could not open the file."
                            elif method_name == "write":
                                if hasattr(obj["handle"], "write") and obj["mode"] in ["w", "a", "r+"]:
                                    content = str(resolved_args[0]) if resolved_args else ""
                                    obj["handle"].write(content)
                                    return None, None
                                else:
                                    return None, "File error: cannot write to the file (permission or mode error)."
                            elif method_name == "read":
                                if hasattr(obj["handle"], "read") and ("r" in obj["mode"] or "+" in obj["mode"]):
                                    obj["handle"].seek(0)
                                    if resolved_args:
                                        n_val = resolved_args[0]
                                        if not isinstance(n_val, int) or isinstance(n_val, bool):
                                            return None, "Logic error: read() argument must be an integer (number of characters)."
                                        if n_val < 0:
                                            return None, "Logic error: read() argument cannot be negative."
                                        content = obj["handle"].read(n_val)
                                    else:
                                        content = obj["handle"].read()
                                    return content, None
                                else:
                                    return None, "File error: cannot read the file (permission or mode error)."
                            elif method_name == "startswith":
                                if not resolved_args: return None, "Error: startswith() requires an argument."
                                return obj["name"].startswith(str(resolved_args[0])), None
                            return None, f"Logic error: file object has no method '{method_name}'."
                    
                    if isinstance(obj, dict) and "type" in obj and obj.get("__type__") not in ("instance", "file"):
                        if method_name == "Move":
                            if len(resolved_args) != 2: return None, "Logic error: Move(dx, dy) requires exactly 2 arguments."
                            try: dx, dy = int(resolved_args[0]), int(resolved_args[1])
                            except (TypeError, ValueError): return None, "Logic error: Move(dx, dy) arguments must be numbers."
                            obj["x"] = obj.get("x", 0) + dx
                            obj["y"] = obj.get("y", 0) + dy
                            return None, None
                        if method_name == "Coordinate":
                            if len(resolved_args) != 2: return None, "Logic error: Coordinate(x, y) requires exactly 2 arguments."
                            try: nx, ny = int(resolved_args[0]), int(resolved_args[1])
                            except (TypeError, ValueError): return None, "Logic error: Coordinate(x, y) arguments must be numbers."
                            obj["x"], obj["y"] = nx, ny
                            return None, None
                        if method_name == "Config":
                            if len(resolved_args) != 1: return None, "Logic error: Config(text) requires exactly 1 argument."
                            obj["text"] = str(resolved_args[0])
                            return None, None
                        if method_name == "Destroy":
                            if obj in self.game_objects: self.game_objects.remove(obj)
                            for k, v in list(self.named_objects.items()):
                                if v is obj: del self.named_objects[k]
                            if self.current_obj is obj: self.current_obj = None
                            return None, None
                    if method_name == "startswith":
                        if not resolved_args: return None, "Error: startswith() requires an argument."
                        arg_val = resolved_args[0]
                        if isinstance(obj, (list, tuple)):
                            if not obj: return False, None
                            if isinstance(arg_val, (list, tuple, dict)):
                                return any(el == arg_val for el in obj), None
                            return any(str(el).startswith(str(arg_val)) for el in obj), None
                        elif isinstance(obj, dict):
                            if obj.get("__type__") == "file":
                                return obj["name"].startswith(str(arg_val)), None
                            elif obj.get("__type__") in ["instance", "game_score"]:
                                return False, None
                            
                            if not obj: return False, None
                            
                            if isinstance(arg_val, dict) and "__dict_pair__" in arg_val:
                                k_str, v_str = arg_val["__dict_pair__"].split(":", 1)
                                k_val, _ = self.resolve_general_value(k_str)
                                v_val, _ = self.resolve_general_value(v_str)
                                for k, v in obj.items():
                                    if k == k_val and v == v_val: return True, None
                                return False, None
                            elif isinstance(arg_val, dict) and arg_val.get("__type__") not in ["instance", "file"]:
                                for arg_k, arg_v in arg_val.items():
                                    for k, v in obj.items():
                                        if k == arg_k and v == arg_v: return True, None
                                return False, None
                            
                            return any(str(k).startswith(str(arg_val)) or str(v).startswith(str(arg_val)) for k, v in obj.items()), None
                        else:
                            return str(obj).startswith(str(arg_val)), None
                            
                    elif method_name == "replace":
                        if len(resolved_args) != 2: return None, "Logic error: replace requires exactly 2 arguments (index1, index2)."
                        idx1, idx2 = resolved_args[0], resolved_args[1]
                        
                        if not isinstance(idx1, int) or not isinstance(idx2, int):
                            try: idx1, idx2 = int(idx1), int(idx2)
                            except ValueError: return None, "Logic error: indices must be integers."
                            
                        if isinstance(obj, bool): return None, "Error: replace cannot be used on bool type."
                        elif isinstance(obj, dict) and obj.get("__type__") == "file": return None, "Error: replace cannot be used on file type."
                        elif isinstance(obj, list):
                            if 0 <= idx1 < len(obj) and 0 <= idx2 < len(obj):
                                if idx1 == idx2:
                                    fill = obj[idx1]
                                    for k in range(len(obj)): obj[k] = fill
                                else:
                                    obj[idx1], obj[idx2] = obj[idx2], obj[idx1]
                                return obj, None
                            return None, "Error: Index does not exist."
                        elif isinstance(obj, tuple):
                            if 0 <= idx1 < len(obj) and 0 <= idx2 < len(obj):
                                if idx1 == idx2:
                                    return (obj[idx1],) * len(obj), None
                                l = list(obj)
                                l[idx1], l[idx2] = l[idx2], l[idx1]
                                return tuple(l), None
                            return None, "Error: Index does not exist."
                        elif isinstance(obj, dict):
                            if obj.get("__type__") in ["instance", "game_score"]: return None, "Error"
                            items = list(obj.items())
                            if 0 <= idx1 < len(items) and 0 <= idx2 < len(items):
                                items[idx1], items[idx2] = items[idx2], items[idx1]
                                new_dict = dict(items)
                                if actual_obj_name in self.variables: self.variables[actual_obj_name] = new_dict
                                return ",".join([f"{k}:{v}" for k, v in new_dict.items()]), None
                            return None, "Error: Index does not exist."
                        elif isinstance(obj, str):
                            if 0 <= idx1 < len(obj) and 0 <= idx2 < len(obj):
                                if idx1 == idx2:
                                    res = obj[idx1] * len(obj)
                                else:
                                    l = list(obj)
                                    l[idx1], l[idx2] = l[idx2], l[idx1]
                                    res = "".join(l)
                                if actual_obj_name in self.variables: self.variables[actual_obj_name] = res
                                return res, None
                            return None, "Error: Index does not exist."
                        elif isinstance(obj, int):
                            s_obj = str(abs(obj))
                            sign = "-" if obj < 0 else ""
                            if 0 <= idx1 < len(s_obj) and 0 <= idx2 < len(s_obj):
                                if idx1 == idx2:
                                    res_str = sign + s_obj[idx1] * len(s_obj)
                                else:
                                    l = list(s_obj)
                                    l[idx1], l[idx2] = l[idx2], l[idx1]
                                    res_str = sign + "".join(l)
                                if actual_obj_name in self.variables: self.variables[actual_obj_name] = int(res_str)
                                return res_str, None
                            return None, "Error: Index does not exist."
                        elif isinstance(obj, float):
                            s_obj = str(obj)
                            sign = "-" if s_obj.startswith("-") else ""
                            s_obj_clean = s_obj.replace("-", "").replace(".", "")
                            if 0 <= idx1 < len(s_obj_clean) and 0 <= idx2 < len(s_obj_clean):
                                if idx1 == idx2:
                                    l = list(s_obj_clean[idx1] * len(s_obj_clean))
                                else:
                                    l = list(s_obj_clean)
                                    l[idx1], l[idx2] = l[idx2], l[idx1]
                                dot_idx = s_obj.find(".")
                                if dot_idx != -1:
                                    if sign: dot_idx -= 1
                                    l.insert(dot_idx, ".")
                                res_str = sign + "".join(l)
                                try: res = float(res_str)
                                except ValueError: res = res_str
                                if actual_obj_name in self.variables: self.variables[actual_obj_name] = res
                                return res_str, None 
                            return None, "Error: Index does not exist."
                        else:
                            return None, "Logic error: replace operation cannot be performed on this data type."
                    
                    if isinstance(obj, list):
                        if method_name == "add":
                            if len(resolved_args) != 1: return None, "Logic error: add() takes exactly 1 argument."
                            obj.insert(0, resolved_args[0])
                            return None, None
                        elif method_name in ["append", "insert"]:
                            if len(resolved_args) != 2: return None, f"Logic error: {method_name}() takes exactly 2 arguments."
                            idx, val = resolved_args[0], resolved_args[1]
                            if idx in ["begin", '"begin"', "'begin'"]: idx = 0
                            elif idx in ["end", '"end"', "'end'"]: idx = len(obj)
                            elif isinstance(idx, (int, float)): idx = int(idx)
                            elif isinstance(idx, str) and idx.lstrip('-').isdigit(): idx = int(idx)
                            else: return None, "Logic error: index must be a number, 'begin', or 'end'."
                            obj.insert(idx, val)
                            return None, None
                        elif method_name == "remove":
                            if len(resolved_args) != 1: return None, "Logic error: remove() takes exactly 1 argument."
                            arg_val = resolved_args[0]
                            
                            if isinstance(arg_val, dict) and "__kwarg_name__" in arg_val:
                                target_val_str = arg_val["__kwarg_name__"]
                                target_val, _ = self.resolve_general_value(target_val_str, allow_raw_string=True)
                                if target_val in obj:
                                    obj.remove(target_val)
                                    return None, None
                                return None, "Error: element not in list."
                            
                            idx = arg_val
                            if idx in ["begin", '"begin"', "'begin'"]:
                                if obj: obj.pop(0)
                            elif idx in ["end", '"end"', "'end'"]:
                                if obj: obj.pop()
                            elif isinstance(idx, (int, float)) or (isinstance(idx, str) and idx.lstrip('-').isdigit()):
                                idx = int(idx)
                                if 0 <= idx < len(obj) or -len(obj) <= idx < 0:
                                    obj.pop(idx)
                                else:
                                    return None, "Error: Index does not exist."
                            else:
                                return None, "Error: wrong argument form for remove."
                            return None, None
                                
                    return None, f"Logic error: method '{method_name}' not found or not supported for object '{actual_obj_name}'."
                return None, f"Logic error: unassigned variable '{actual_obj_name}'."

            if fn_name not in self.functions: return None, f"Statistics error (Line {line_num}): no such function found: '{fn_name}'"
                
            if self.functions[fn_name].get("__type__") == "py_function":
                try:
                    res = self.functions[fn_name]["func"](*resolved_args)
                    return res, None
                except Exception as e:
                    return None, f"Python function error: {str(e)}"

            return self.execute_function_with_values(fn_name, resolved_args)
        except Exception as e:
            return None, f"Unexpected error: {str(e)}"

    def execute_lines(self, lines):
        i = 0
        skip_indent = None
        cond_state_stack = [] 
        last_executed_line = None 
        
        while i < len(lines):
            if self.should_stop: break
            line_num, indent, line = lines[i]
            i += 1
            self._lines_executed_total = getattr(self, "_lines_executed_total", 0) + 1
            now_t = time.time()
            elapsed = now_t - getattr(self, "_run_start_time", now_t)
            if elapsed > 0:
                self.lines_per_second = self._lines_executed_total / elapsed
            
            if skip_indent is not None:
                if indent >= skip_indent: continue
                else: skip_indent = None
                    
            cond_states_to_keep = []
            for state in cond_state_stack:
                if state['indent'] < indent: cond_states_to_keep.append(state)
                elif state['indent'] == indent and line.startswith(("Choose ", "Fail:")): cond_states_to_keep.append(state)
            cond_state_stack = cond_states_to_keep

            from_import_match = re.match(r'^from\s+([\w.]+)\s+import\s+(.+)$', line)
            if from_import_match:
                err = self.handle_from_import(from_import_match.group(1).strip(), from_import_match.group(2).strip(), line_num)
                if err: return None, f"Statistics error (Line {line_num}): {err}", "error"
                continue

            if line.startswith("import "):
                err = self.handle_import_multi(line[7:].strip(), line_num)
                if err: return None, f"Statistics error (Line {line_num}): {err}", "error"
                continue
            
            if line.startswith("wait(") and line.endswith(")"):
                if not getattr(self, 'ilusaztar_imported', False):
                    return None, f"Statistics error (Line {line_num}): the 'ilusaztar' package must be installed for game functions!", "error"
                val_str = line[5:-1].strip()
                val, err = self.resolve_general_value(val_str, allow_raw_string=True)
                if err: return None, f"Statistics error (Line {line_num}): {err}", "error"
                if isinstance(val, (int, float)):
                    time.sleep(val)
                else: return None, f"Error (Line {line_num}): wait() only accepts a number.", "error"
                continue
            
            if line in ["break", "continue", "pass"] or line.startswith("break "):
                if line == "pass": continue
                is_valid_placement = False
                next_is_repeat = False
                if i < len(lines):
                    next_line_num, next_indent, next_line = lines[i]
                    if next_line.startswith("Repeat:") and next_indent == indent:
                        is_valid_placement = True
                        next_is_repeat = True

                if not is_valid_placement:
                    return None, f"Statistics error (Line {line_num}): break/continue can only be used inside a loop (immediately before a Repeat:, or as a Target list-filter body).", "error"
                if next_is_repeat:
                    if line.startswith("break"):
                        self.log("System: loop stopped early (break).")
                        skip_indent = indent + 1
                        i += 1  
                        continue
                    elif line == "continue":
                        self.log("System: Continuing...")
                        continue  
                else:
                    if line == "break": return None, None, "break"
                    if line.startswith("break "):
                        func_target = line[6:].strip()
                        self.log(f"System: {func_target} stopped.")
                        return None, None, "break"
                    if line == "continue":
                        self.log("System: Continuing...")
                        continue

            if line.startswith("del "):
                err = self.handle_del(line, line_num)
                if err: return None, f"Statistics error (Line {line_num}): {err}", "error"
                continue

            if line.startswith("return ") or line == "return":
                if line == "return": return None, None, "return"
                ret_expr = line[7:].strip()
                if not ret_expr: return None, f"Statistics error (Line {line_num}): return value is empty.", "return"
                val, err = self.resolve_general_value(ret_expr, allow_raw_string=False)
                if err: return None, f"Statistics error (Line {line_num}): {err}", "return"
                return val, None, "return"
                
            fn_match = re.match(r'^(\*?[\w.]+)\((.*)\)$', line)
            if fn_match and fn_match.group(1) not in ["write", "work", "UI", "wait"]:
                fn_name = fn_match.group(1)
                arg_str = fn_match.group(2)
                if fn_name in ["free", "abs", "round", "open", "zip", "think", "locals", "globals", "len", "max", "min", "type", "check", "id", "int", "str", "fround", "bool", "list", "dict", "tuple", "TYPE", "setattr", "getattr"]:
                    val, err = self.resolve_general_value(line, allow_raw_string=True)
                    if err: return None, f"Statistics error (Line {line_num}): {err}", "error"
                    continue
                elif fn_name in self.functions or fn_name in self.classes or ("." in fn_name):
                    ret_val, err = self.execute_possibly_chained_call(line, line_num)
                    if err: return None, err, "error"
                    if not line.startswith(("Target ", "Choose ", "Fail:", "Repeat:")): last_executed_line = line
                    continue
                
            if line.startswith("Target "):
                if not line.endswith(":"): return None, f"Statistics error (Line {line_num}): Target line must end with ':'.", "error"
                cond_str = line[7:-1].strip()

                # If the condition references a variable currently holding a list, and a single
                # indented body line follows, treat this as a per-element filter+iterate rather
                # than whole-list arithmetic (e.g. Target s % 2 == 0: / write(s), where s is a list).
                list_var_name = None
                for tok in re.findall(r'[A-Za-z_]\w*', cond_str):
                    if tok in ILUS_KEYWORDS or tok in ("and", "or", "is", "not", "in"): continue
                    tok_val = self.variables.get(tok)
                    if isinstance(tok_val, list):
                        list_var_name = tok
                        break

                if list_var_name is not None and i < len(lines) and lines[i][1] > indent:
                    body_line_num, body_indent, body_line = lines[i]
                    i += 1
                    original_list = self.variables[list_var_name]
                    try:
                        for item in original_list:
                            self.variables[list_var_name] = item
                            cond_res, cond_err = self.evaluate_condition(cond_str)
                            if cond_err or not cond_res: continue
                            status = self.execute_statement_by_string(body_line, body_line_num)
                            if status == "break": break
                            elif status == "continue": continue
                            elif status: return None, f"Statistics error (Line {body_line_num}): {status}", "error"
                    finally:
                        self.variables[list_var_name] = original_list
                    continue

                res, err = self.evaluate_condition(cond_str)
                if err: return None, f"Statistics error (Line {line_num}): {err}", "error"
                cond_state_stack.append({'indent': indent, 'matched': res, 'chain_active': True})
                if not res: skip_indent = indent + 3
                continue
                
            elif line.startswith("Choose "):
                if not line.endswith(":"): return None, f"Statistics error (Line {line_num}): Choose line must end with ':'.", "error"
                if not cond_state_stack or cond_state_stack[-1]['indent'] != indent or not cond_state_stack[-1]['chain_active']:
                    return None, f"Statistics error (Line {line_num}): Target must be used at the same indentation level right before Choose.", "error"
                state = cond_state_stack[-1]
                if state['matched']: skip_indent = indent + 3
                else:
                    cond_str = line[7:-1].strip()
                    res, err = self.evaluate_condition(cond_str)
                    if err: return None, f"Statistics error (Line {line_num}): {err}", "error"
                    if res: state['matched'] = True
                    else: skip_indent = indent + 3
                continue
                
            elif line.startswith("Fail:"):
                if line != "Fail:": return None, f"Statistics error (Line {line_num}): Fail block must be exactly in 'Fail:' format.", "error"
                if not cond_state_stack or cond_state_stack[-1]['indent'] != indent or not cond_state_stack[-1]['chain_active']:
                    return None, f"Statistics error (Line {line_num}): Target must be used at the same indentation level right before Fail.", "error"
                state = cond_state_stack[-1]
                if state['matched']: skip_indent = indent + 3
                state['chain_active'] = False
                continue

            elif line.startswith("Repeat:"):
                match = re.search(r'^Repeat:\s*\((.*)\)$', line)
                if not match: return None, f"Statistics error (Line {line_num}): Repeat format is wrong.", "error"
                val_str = match.group(1).strip()
                if val_str in ["true", "false", "none"]:
                    if val_str not in self.variables:
                        return None, f"Statistics error (Line {line_num}): '{val_str}' cannot be counted as bool function! Correct usage: {val_str.capitalize()}", "error"
                if " in " in val_str:
                    parts = val_str.split(" in ", 1)
                    var_name = parts[0].strip()
                    if var_name in ILUS_KEYWORDS:
                        return None, f"Statistics error (Line {line_num}): '{var_name}' is a keyword and cannot be used as a variable name!", "error"
                    list_name = parts[1].strip()
                    lst, err = self.resolve_general_value(list_name, allow_raw_string=False)
                    if err: return None, f"Logic error (Line {line_num}): {err}", "error"
                    
                    if isinstance(lst, dict) and lst.get("__type__") == "instance":
                        cls_name = lst["__class__"]
                        if "__walk__" in self.classes[cls_name]["methods"]:
                            res, m_err = self.execute_method_with_values(lst, cls_name, "__walk__", [])
                            if m_err: return None, f"Logic error (Line {line_num}): {m_err}", "error"
                            lst = res
                        else:
                            return None, f"Logic error (Line {line_num}): class '{cls_name}' has no '__walk__', cannot be iterated.", "error"
                            
                    lst_iter, unpack_err = self.unpack_for_iteration(lst)
                    if unpack_err: return None, f"Logic error (Line {line_num}): {unpack_err}", "error"

                    for item in lst_iter:
                        self.variables[var_name] = item
                        self.log(item)
                        if last_executed_line:
                            status = self.execute_statement_by_string(last_executed_line, line_num)
                            if status == "break": break
                            elif status == "continue": continue
                            elif status: return None, f"Statistics error (Line {line_num}): {status}", "error"
                    continue

                if "," in val_str:
                    res, err = self.evaluate_repeat(line)
                    if err: return None, f"Statistics error (Line {line_num}): {err}", "error"
                    if res is not None:
                        # Fix #2: Target filtering logic with Repeat
                        for num in res:
                            self.variables["_"] = num
                            # If Target has a condition, check it first
                            if last_executed_line and last_executed_line.startswith("Target "):
                                cond_str = last_executed_line.split("Target ", 1)[1]
                                cond_res, cond_err = self.evaluate_condition(cond_str)
                                if cond_err or not cond_res: continue  # skip this iteration
                            
                            self.log(num)
                            if last_executed_line and not last_executed_line.startswith("Target "):
                                status = self.execute_statement_by_string(last_executed_line, line_num)
                                if status == "break": break
                                elif status == "continue": continue
                                elif status: return None, f"Statistics error (Line {line_num}): {status}", "error"
                    continue
                
                if any(op in val_str for op in ["==", "!=", "<=", ">=", "<", ">"]) or val_str in ["True", "False"]:
                    while not self.should_stop:
                        if any(op in val_str for op in ["==", "!=", "<=", ">=", "<", ">"]): res, err = self.evaluate_condition(val_str)
                        else: res, err = (True if val_str == "True" else False, None)
                        if err: return None, f"Statistics error (Line {line_num}): {err}", "error"
                        if not res: break
                        
                        if last_executed_line:
                            status = self.execute_statement_by_string(last_executed_line, line_num)
                            if status == "break": break
                            elif status == "continue": continue
                            elif status: return None, f"Statistics error (Line {line_num}): {status}", "error"
                        else: time.sleep(0.02)
                        time.sleep(0.02)
                    continue
                else:
                    times_val, err = self.resolve_general_value(val_str, allow_raw_string=True)
                    if err: return None, f"Statistics error (Line {line_num}): {err}", "error"
                    if isinstance(times_val, (int, float)): times = int(times_val)
                    elif isinstance(times_val, str) and times_val.lstrip('-').isdigit(): times = int(times_val)
                    else: return None, f"Logic error (Line {line_num}): repeat count must be an integer.", "error"
                    
                    if times > 0 and last_executed_line:
                        for _ in range(times - 1):
                            status = self.execute_statement_by_string(last_executed_line, line_num)
                            if status == "break": break
                            elif status == "continue": continue
                            elif status: return None, f"Statistics error (Line {line_num}): {status}", "error"
                    continue

            if line in ["work Sx.game", "work Area.game"]:
                if not getattr(self, 'ilusaztar_imported', False): return None, f"Statistics error (Line {line_num}): the 'ilusaztar' package must be installed for game functions!", "error"
                if self.game_slot_active: return None, f"Statistics error (Line {line_num}): the game slot is already active.", "error"
                self.game_slot_active = True
                self.log(f"System: {line} activated.")
            elif line.startswith(("Create:", "Set:", "Size:", "Color:", "Text:", "Shape:", "Text.Color:", "Text.Size:", "UI(", "Score:", "Score.Color:", "Score.Size:", "Anchored:", "Collision:", "Transparency:", "GUI", "move:", "End(", "sup_devices(")) or re.match(r'^\w+\.(Create:|Size:|Color:|Text:|Shape:|Text\.Color:|Text\.Size:|Set:|Score:|Score\.Color:|Score\.Size:|Anchored:|Collision:|Transparency:)', line):
                if not getattr(self, 'ilusaztar_imported', False): return None, f"Statistics error (Line {line_num}): the 'ilusaztar' package must be installed for game functions!", "error"
                err = self.execute_game_command(line, line_num)
                if err: return None, f"Statistics error (Line {line_num}): {err}", "error"
            elif re.match(r'^[A-Za-z_]\w*\.(?:Text\.Color|Text\.Size|Score\.Size|Score\.Color|Create|Size|Color|Text|Set|Score|Anchored|Transparency|Collision):', line):
                if not getattr(self, 'ilusaztar_imported', False): return None, f"Statistics error (Line {line_num}): the 'ilusaztar' package must be installed for game functions!", "error"
                _, err = self.try_parent_prefixed_command(line, line_num)
                if err: return None, f"Statistics error (Line {line_num}): {err}", "error"
            elif line.startswith("work("):
                val, err = self.handle_work_syntax(line)
                if err: return None, f"Statistics error (Line {line_num}): {err}", "error"
                if val is not None: self.log(val)
            elif line.startswith("write("):
                _, err = self.handle_write(line)
                if err: return None, f"Statistics error (Line {line_num}): {err}", "error"
            elif re.match(r'^(free|abs|round|open|zip|think|locals|globals|len|max|min|type|check|id|int|str|fround|bool|list|dict|tuple|TYPE)\(', line):
                val, err = self.resolve_general_value(line, allow_raw_string=True)
                if err: return None, f"Statistics error (Line {line_num}): {err}", "error"
            elif re.search(r'\s*(\+=|-=|\*\*=|\*=|//=|/=|%=|\^=)\s*', line) or "=" in line:
                err = self.handle_assignment(line, line_num)
                if err: return None, f"Statistics error (Line {line_num}): {err}", "error"
            else:
                val, err = self.resolve_general_value(line, allow_raw_string=True)
                if not err:
                    self.variables["_"] = val
                else:
                    return None, f"Statistics error (Line {line_num}): unrecognized or incorrectly formatted command: '{line}'", "error"
            
            if not line.startswith(("Target ", "Choose ", "Fail:", "Repeat:", "continue", "break", "pass", "del ", "wait(")):
                last_executed_line = line
                
        return None, None, "success"

    def run(self, code):
        if not code or not code.strip(): return "System: No code entered."
        self.functions = {}
        self.classes = {}
        self.should_stop = False
        self.logs = []
        self.variables = {}
        self.variable_origins = {}
        self.game_objects = []
        self.game_slot_active = False
        self.should_end_game = False
        self.supported_devices = {"M": True, "P": True}
        self.lines_per_second = 0
        self._lines_executed_total = 0
        self._run_start_time = time.time()
        self.current_obj = None
        self.pc_controls = []
        self.pc_control_binds = {}
        self.ilusaztar_imported = False
        
        self.open_files = []
        
        try:
            raw_lines = code.split('\n')
            parsed_lines = []
            indent_stack = [0]
            expecting_block = False
            expecting_module_decorator = False
            
            for i, raw_line in enumerate(raw_lines):
                line_num = i + 1
                if not raw_line.strip(): continue
                    
                raw_line_no_tabs = raw_line.replace('\t', '   ')
                indent = len(raw_line_no_tabs) - len(raw_line_no_tabs.lstrip(' '))
                stripped = raw_line_no_tabs.strip()
                
                if indent % 3 != 0:
                    self.log(f"High-level permission error (Line {line_num}): a block must have exactly 3 spaces of indentation. You have {indent} spaces.")
                    self.should_stop = True
                    return "\n".join(self.logs)
                    
                if expecting_module_decorator:
                    if indent == indent_stack[-1]:
                        pass  # decorator flush with its target (standard decorator style)
                    elif indent == indent_stack[-1] + 3:
                        indent_stack.append(indent)  # decorator nesting its target one level deeper
                    else:
                        self.log(f"High-level permission error (Line {line_num}): the line after @__module__ must either match its indentation or start exactly 3 spaces ahead of it.")
                        self.should_stop = True
                        return "\n".join(self.logs)
                    expecting_module_decorator = False
                elif expecting_block:
                    if indent != indent_stack[-1] + 3:
                        self.log(f"High-level permission error (Line {line_num}): a block line must start exactly 3 spaces ahead of the previous one.")
                        self.should_stop = True
                        return "\n".join(self.logs)
                    indent_stack.append(indent)
                    expecting_block = False
                else:
                    while len(indent_stack) > 1 and indent < indent_stack[-1]: indent_stack.pop()
                    if indent != indent_stack[-1]:
                        self.log(f"High-level permission error (Line {line_num}): unexpected space. The block was not exited correctly, or an extra space was added.")
                        self.should_stop = True
                        return "\n".join(self.logs)
                        
                parsed_lines.append((line_num, indent, stripped))
                if stripped == "@__module__":
                    expecting_module_decorator = True
                elif stripped.endswith(":"): expecting_block = True
                    
            if expecting_block or expecting_module_decorator:
                self.log(f"High-level permission error: a block was started at the very end, but its body is empty.")
                self.should_stop = True
                return "\n".join(self.logs)
                
            for line_num, indent, stripped in parsed_lines:
                if not self.check_line_syntax_only(stripped):
                    self.log(f"Statistics error (Line {line_num}): syntax rules violated, structure incomplete, or a forbidden command (e.g. wrong indentation) was used: '{stripped}'")
                    self.should_stop = True
                    return "\n".join(self.logs)

            global_lines = []
            class_stack = []
            current_func = None
            pending_module_only = False
            self._module_only_names = set()
            self._module_only_class_names = set()
            
            for line_num, indent, stripped in parsed_lines:
                while class_stack and indent <= class_stack[-1][1]:
                    class_stack.pop()
                    
                if current_func and indent <= current_func["indent"]:
                    current_func = None
                
                current_class_name = ".".join([c[0] for c in class_stack]) if class_stack else None

                if not current_func and stripped == "@__module__":
                    pending_module_only = True
                    continue
                
                if current_func:
                    if current_class_name: 
                        self.classes[current_class_name]["methods"][current_func["name"]]["body"].append((line_num, indent, stripped))
                    else: 
                        self.functions[current_func["name"]]["body"].append((line_num, indent, stripped))
                    continue
                
                if stripped.startswith("class "):
                    match = re.match(r'^class\s+([\w.]+)(?:\(([\w_.]+)\))?:$', stripped)
                    if match:
                        new_class = match.group(1)
                        parent_name = match.group(2)
                        
                        full_class_name = new_class
                        if class_stack:
                            full_class_name = current_class_name + "." + new_class
                            
                        self.classes[full_class_name] = {"methods": {}, "parent": parent_name if parent_name else None}
                        
                        # Fix #5: Class inheritance - inherit the parent class's methods
                        if parent_name:
                            parent_found = False
                            # First check the parent name directly
                            if parent_name in self.classes:
                                for m_name, m_data in self.classes[parent_name]["methods"].items():
                                    self.classes[full_class_name]["methods"][m_name] = m_data.copy()
                                parent_found = True
                            else:
                                # search among ancestors
                                for c_name, c_data in self.classes.items():
                                    if c_name.endswith(parent_name) or parent_name in c_name.split("."):
                                        for m_name, m_data in c_data["methods"].items():
                                            self.classes[full_class_name]["methods"][m_name] = m_data.copy()
                                        parent_found = True
                                        break
                            
                            if not parent_found:
                                self.log(f"Warning (Line {line_num}): parent class named '{parent_name}' not found, define it first.")
                                # If no parent found, just give a warning but continue
                            else:
                                self.log(f"Class '{full_class_name}' inherited from class '{parent_name}'.")

                        if pending_module_only and not class_stack:
                            self._module_only_class_names.add(full_class_name)
                        pending_module_only = False

                        class_stack.append((new_class, indent))
                        continue
                    else:
                        self.log(f"Statistics error (Line {line_num}): class definition format is wrong.")
                        self.should_stop = True
                        return "\n".join(self.logs)
                
                elif stripped.startswith("function "):
                    match = re.match(r'^function\s+([\w_]+)\(([^)]*)\):$', stripped)
                    if match:
                        func_name = match.group(1)
                        param_str = match.group(2)
                        
                        params_raw, unbal = self.smart_split(param_str)
                        if unbal:
                            self.log(f"Statistics error (Line {line_num}): Function/Method parameters are unbalanced.")
                            self.should_stop = True
                            return "\n".join(self.logs)
                            
                        parsed_params = []
                        for p in params_raw:
                            if "=" in p:
                                k, v = p.split("=", 1)
                                k, v = k.strip(), v.strip()
                            else:
                                k, v = p.strip(), None
                            param_type = None
                            type_ann = re.fullmatch(r'([A-Za-z_]\w*)\s*:\s*(int|str|fround|bool|list|dict|tuple)', k)
                            if type_ann:
                                k, param_type = type_ann.group(1), type_ann.group(2)
                            parsed_params.append({"name": k, "default": v, "type": param_type})
                                
                        current_func = {"name": func_name, "indent": indent}
                        if current_class_name:
                            self.classes[current_class_name]["methods"][func_name] = {"params": parsed_params, "body": []}
                        else:
                            self.functions[func_name] = {"params": parsed_params, "body": [], "def_line": line_num}
                            if pending_module_only:
                                self._module_only_names.add(func_name)
                        pending_module_only = False
                        continue
                    else:
                        self.log(f"Statistics error (Line {line_num}): Function/Method definition format is wrong.")
                        self.should_stop = True
                        return "\n".join(self.logs)
                
                elif stripped == "pass":
                    if class_stack: continue
                    else: global_lines.append((line_num, indent, stripped))
                else:
                    if class_stack:
                        self.log(f"Statistics error (Line {line_num}): only methods ('function') or another 'class' can be written inside a Class block.")
                        self.should_stop = True
                        return "\n".join(self.logs)
                    
                    global_lines.append((line_num, indent, stripped))

            ret_val, err, signal = self.execute_lines(global_lines)
            if err:
                self.log(err)
                self.should_stop = True
                return "\n".join(self.logs)
                
            return "\n".join(self.logs)
            
        except Exception as err_msg:
            import traceback
            critical_err = f"Kritik Error: {str(err_msg)}\n{traceback.format_exc()}"
            self.log(critical_err)
            return critical_err
            
        finally:
            for f in self.open_files:
                if hasattr(f, "close"):
                    f.close()
            gc.collect()

    def handle_assignment(self, line, line_num):
        # New feature: `variable: type = value` typed assignment.
        # e.g. number: int = 20
        type_ann_match = re.fullmatch(r'^([A-Za-z_]\w*)\s*:\s*(int|str|fround|bool|list|dict|tuple)\s*=\s*(.*)$', line)
        if type_ann_match:
            var_name, type_name, rhs = type_ann_match.groups()
            if var_name in ILUS_KEYWORDS:
                return f"Statistics error: '{var_name}' is a keyword and cannot be used as a variable name!"
            rhs = rhs.strip()
            if not rhs: return f"Logic error: no value given for '{var_name}: {type_name}'."
            val, err = self.resolve_general_value(rhs, allow_raw_string=True)
            if err: return err
            py_types = {"int": int, "str": str, "fround": float, "bool": bool, "list": list, "dict": dict, "tuple": tuple}
            expected = py_types[type_name]
            type_ok = isinstance(val, expected) and not (type_name != "bool" and isinstance(val, bool))
            if type_name == "fround" and isinstance(val, int) and not isinstance(val, bool):
                # allow a plain int literal to satisfy a fround (float) annotation
                val = float(val)
                type_ok = True
            if not type_ok:
                return f"Logic error: '{var_name}' is declared as type '{type_name}', but the value is of type '{type(val).__name__}'."
            self.variables[var_name] = val
            self.variable_origins[var_name] = type_name
            return None

        # `target = Set: "entity" = user.id = 1918/int` uses '=' as part of its own
        # syntax, so it must be pulled out before the generic multi-'=' splitter
        # below (safe_split_all_assignments) mistakes its pieces for assignment
        # chain targets.
        set_id_match = re.fullmatch(
            r'^((?:[A-Za-z_]\w*\s*=\s*)+)(Set:\s*"(?:entity|object)"\s*=\s*user\.id\s*=\s*(?:-?\d+|int))$', line)
        if set_id_match:
            if not getattr(self, 'ilusaztar_imported', False): return "The 'ilusaztar' module must be imported for game functions!"
            targets = re.findall(r'[A-Za-z_]\w*', set_id_match.group(1))
            cmd_err = self.execute_game_command(set_id_match.group(2), line_num)
            if cmd_err: return cmd_err
            id_val = self.current_obj.get("custom_id")
            for t in targets:
                if t in ILUS_KEYWORDS: return f"Statistics error: '{t}' is a keyword and cannot be used as a variable name!"
                self.variables[t] = id_val
                self.variable_origins[t] = "int"
            return None

        comp_match = re.fullmatch(r'^([^=]+?)\s*(\+=|-=|\*\*=|\*=|//=|/=|%=|\^=)\s*(.*)$', line)
        if comp_match:
            var_name = comp_match.group(1).strip()
            op_str = comp_match.group(2).strip()
            op = op_str[0] if op_str[1] == '=' else op_str[1]
            if op_str == '//=': op = '//'
            elif op_str == '**=': op = '**'
            
            right_expr = comp_match.group(3).strip()
            right_val, err = self.resolve_general_value(right_expr, allow_raw_string=False)
            if err: return err
            
            left_val = None
            clean_part = var_name
            if var_name.startswith("table."): clean_part = var_name[6:]
            attr_match = re.fullmatch(r'^(\w+)\.(\w+)$', clean_part)
            idx_match = re.fullmatch(r'^([\w.]+)\[(.*)\]$', clean_part)
            
            if attr_match:
                obj_name, attr_name = attr_match.groups()
                if obj_name in self.variables:
                    obj = self.variables[obj_name]
                    if isinstance(obj, dict) and obj.get("__type__") == "instance":
                        left_val = obj["attrs"].get(attr_name)
                    elif isinstance(obj, dict) and obj.get("__type__") == "game_score" and attr_name == "Value":
                        left_val = obj["attrs"]["Value"]
            elif idx_match:
                l_name, idx_str = idx_match.groups()
                obj, err_obj = self.resolve_general_value(l_name, allow_raw_string=True)
                if not err_obj and obj is not None:
                    idx_val, err_idx = self.resolve_general_value(idx_str, allow_raw_string=True)
                    if not err_idx:
                        if isinstance(obj, list) and isinstance(idx_val, int) and 0 <= idx_val < len(obj):
                            left_val = obj[idx_val]
                        elif isinstance(obj, dict):
                            if idx_val in obj:
                                left_val = obj[idx_val]
            else:
                left_val, _ = self.resolve_general_value(var_name, allow_raw_string=True)
                
            if left_val is None: return f"Logic error: '{var_name}' is not assigned or not found."
            
            try:
                if isinstance(left_val, list) and isinstance(right_val, (int, float)):
                    if op == '+': final_val = [x + right_val for x in left_val]
                    elif op == '-': final_val = [x - right_val for x in left_val]
                    elif op == '*': final_val = [x * right_val for x in left_val]
                    elif op == '/': final_val = [x / right_val for x in left_val]
                    elif op == '//': final_val = [x // right_val for x in left_val]
                    elif op == '%': final_val = [x % right_val for x in left_val]
                    elif op == '**': final_val = [x ** right_val for x in left_val]
                    else: return "Logic error: this operator is not supported on lists."
                else:
                    if op == '+': final_val = left_val + right_val
                    elif op == '-': final_val = left_val - right_val
                    elif op == '*': final_val = left_val * right_val
                    elif op == '/': final_val = left_val / right_val
                    elif op == '//': final_val = left_val // right_val
                    elif op == '%': final_val = left_val % right_val
                    elif op == '**': final_val = left_val ** right_val
                    elif op == '^':
                        if isinstance(left_val, int) and isinstance(right_val, int): final_val = left_val ^ right_val
                        else: return "Logic error: XOR (^) only works with 'int' types."
            except Exception as e:
                return f"Logic error: math operation error: {str(e)}"

            if attr_match:
                obj_name, attr_name = attr_match.groups()
                if obj_name in self.variables:
                    obj = self.variables[obj_name]
                    if isinstance(obj, dict) and obj.get("__type__") == "instance":
                        obj["attrs"][attr_name] = final_val
                    elif isinstance(obj, dict) and obj.get("__type__") == "game_score" and attr_name == "Value":
                        score_obj = obj["obj"]
                        if isinstance(final_val, (int, float)):
                            final_val = max(0, min(int(final_val), score_obj["max_val"]))
                            score_obj["value"] = final_val
                            obj["attrs"]["Value"] = final_val
                        else: return "Logic error: Score must be an integer (int)."
                else: return f"Logic error: '{obj_name}' not found."
            elif idx_match:
                l_name, idx_str = idx_match.groups()
                obj, _ = self.resolve_general_value(l_name, allow_raw_string=True)
                idx_val, _ = self.resolve_general_value(idx_str, allow_raw_string=True)
                if isinstance(obj, list):
                    if isinstance(idx_val, str) and idx_val.lstrip('-').isdigit(): idx_val = int(idx_val)
                    if isinstance(idx_val, int):
                        if 0 <= idx_val < len(obj) or -len(obj) <= idx_val < 0: obj[idx_val] = final_val
                        else: return "Logic error: Index not found."
                    else: return "Logic error: Index must be an integer."
                elif isinstance(obj, dict):
                    if obj.get("__type__") == "instance":
                        cls_name = obj["__class__"]
                        return f"Logic error: class '{cls_name}' does not support index assignment."
                    else:
                        obj[idx_val] = final_val
            else:
                self.variables[var_name] = final_val
            return None

        parts = self.safe_split_all_assignments(line)
        if len(parts) < 2: return "Assignment format is wrong."
        
        if len(parts) == 2 and "," not in parts[0]:
            val_str = parts[1].strip()
            args, unbal = self.smart_split(val_str)
            if not unbal and len(args) > 1 and not (val_str.startswith("(") or val_str.startswith("[")):
                return f"Logic error: single variable ({parts[0].strip()}) cannot be assigned {len(args)} different values! Each variable must correspond to 1 value."
        
        if len(parts) == 2 and "," in parts[0]:
            vars_str = parts[0]
            vals_str = parts[1]
            var_list, unbal1 = self.smart_split(vars_str)
            if unbal1 or not var_list:
                var_list = [v.strip() for v in vars_str.split(",") if v.strip()]
                
            val_list_strs, unbal2 = self.smart_split(vals_str)
            resolved_vals = []
            if not unbal2 and len(val_list_strs) == len(var_list):
                for v_str in val_list_strs:
                    v_val, err = self.resolve_general_value(v_str, allow_raw_string=False)
                    if err: return err
                    resolved_vals.append(v_val)
            else:
                val_obj, err = self.resolve_general_value(vals_str, allow_raw_string=False)
                if err: return err
                if isinstance(val_obj, (list, tuple)) and len(val_obj) == len(var_list): resolved_vals = list(val_obj)
                else: return f"Logic error: number of variables ({len(var_list)}) does not match the number of values."
            
            for idx, (var_name, final_val) in enumerate(zip(var_list, resolved_vals)):
                v_str = val_list_strs[idx] if idx < len(val_list_strs) else vals_str
                origin = None
                if "random:" in v_str: origin = "random"
                elif "coordinat:" in v_str: origin = "coordinat"
                
                clean_var_name = var_name
                if var_name.startswith("table."): clean_var_name = var_name[6:]
                
                attr_match = re.fullmatch(r'^(\w+)\.(\w+)$', clean_var_name)
                if attr_match:
                    obj_name, attr_name = attr_match.groups()
                    if obj_name in self.variables and isinstance(self.variables[obj_name], dict):
                        if self.variables[obj_name].get("__type__") == "instance":
                            self.variables[obj_name]["attrs"][attr_name] = final_val
                            continue
                        elif self.variables[obj_name].get("__type__") == "game_score":
                            if attr_name == "Value":
                                score_obj = self.variables[obj_name]["obj"]
                                if isinstance(final_val, (int, float)):
                                    final_val = max(0, min(int(final_val), score_obj["max_val"]))
                                    score_obj["value"] = final_val
                                    self.variables[obj_name]["attrs"]["Value"] = final_val
                                else:
                                    return "Logic error: Score can only be an integer (int)."
                                continue
                    else:
                        return f"Logic error: '{obj_name}' not found or is not a related object."

                idx_match = re.fullmatch(r'^([\w.]+)\[(.*)\]$', clean_var_name)
                if idx_match:
                    l_name = idx_match.group(1)
                    idx_str = idx_match.group(2).strip()
                    if idx_str in ['"end"', '"begin"', 'end', 'begin', "'end'", "'begin'"]: return "Statistics error: \"end\" or \"begin\" cannot be an index!"
                    
                    obj, err = self.resolve_general_value(l_name, allow_raw_string=True)
                    if err: return err
                    if obj is None: return f"Logic error: '{l_name}' not found."
                    
                    if isinstance(obj, list):
                        idx_val, err_i = self.resolve_general_value(idx_str, allow_raw_string=True)
                        if err_i: return err_i
                        if isinstance(idx_val, (int, float)): idx_val = int(idx_val)
                        elif isinstance(idx_val, str) and idx_val.lstrip('-').isdigit(): idx_val = int(idx_val)
                        else: return f"Logic error: Index must be an integer."
                        
                        if 0 <= idx_val < len(obj) or -len(obj) <= idx_val < 0:
                            obj[idx_val] = final_val
                        else:
                            return f"Logic error: Index not found."
                    elif isinstance(obj, dict):
                        if obj.get("__type__") == "instance":
                            cls_name = obj["__class__"]
                            return f"Logic error: class '{cls_name}' does not support index assignment."
                        idx_val, err_i = self.resolve_general_value(idx_str, allow_raw_string=True)
                        if err_i: return err_i
                        obj[idx_val] = final_val
                    else: return f"Logic error: '{l_name}' is not a list or dict."
                    continue
                elif not re.fullmatch(r'^\*?\w+$', var_name) or not var_name.replace('*', '').isidentifier(): return f"'{var_name}' is not a valid variable name."
                elif var_name in ILUS_KEYWORDS: return f"Statistics error: '{var_name}' is a keyword and cannot be set as a variable name!"
                else:
                    self.variables[var_name] = final_val
                    if origin: self.variable_origins[var_name] = origin
                    elif var_name in self.variable_origins: del self.variable_origins[var_name]
            return None

        final_val = None
        err = None
        assign_targets = []
        origin = None
        
        if len(parts) >= 3 and re.fullmatch(r'(str|int|bool|fround|dict|list|tuple|file)\(\)', parts[-1]):
            target_type = parts[-1][:-2]
            val_str = parts[-2]
            val, err = self.resolve_general_value(val_str, allow_raw_string=False)
            if err: return err
            final_val, err = self.cast_value(target_type, val)
            if err: return err
            assign_targets = parts[:-2]
        else:
            right_most = parts[-1].strip()
            if not right_most: return "The value to be assigned is empty."
            assign_targets = parts[:-1]

            if right_most == "free" and len(assign_targets) == 1:
                target_name = assign_targets[0].strip()
                if target_name.startswith("*"): target_name = target_name[1:]
                if target_name in self.variables:
                    freed_val = self.variables[target_name]
                    if isinstance(freed_val, dict) and freed_val.get("__type__") == "instance":
                        cls_name = freed_val["__class__"]
                        if "__calldelitem__" in self.classes.get(cls_name, {}).get("methods", {}):
                            _, m_err = self.execute_method_with_values(freed_val, cls_name, "__calldelitem__", [])
                            if m_err: return m_err
                    self.variables[target_name] = None
                    self.variable_origins.pop(target_name, None)
                    del freed_val
                    gc.collect()
                return None

            if re.fullmatch(r'Create:\s*"(block|circle|UI|area|GUI)"', right_most) and len(assign_targets) == 1:
                if not getattr(self, 'ilusaztar_imported', False): return "The 'ilusaztar' module must be imported for game functions!"
                err = self.execute_game_command(right_most, line_num)
                if err: return err
                target_name = assign_targets[0].strip()
                self.variables[target_name] = self.current_obj
                self.named_objects[target_name] = self.current_obj
                return None
            
            type_cast_match = re.fullmatch(r'(int|fround|str|bool|dict|list|tuple|file)\(\)', right_most)
            if type_cast_match:
                target_type = type_cast_match.group(1)
                target_name = assign_targets[0].strip()
                if target_name in self.variables: final_val, err = self.cast_value(target_type, self.variables[target_name])
                else:
                    if target_type == "int": final_val = 0
                    elif target_type == "fround": final_val = 0.0
                    elif target_type == "str": final_val = ""
                    elif target_type == "bool": final_val = False
                    elif target_type == "dict": final_val = {}
                    elif target_type == "list": final_val = []
                    elif target_type == "tuple": final_val = ()
                    elif target_type == "file": return "Logic error: file() type cannot be converted from empty data."
            elif right_most in ["work Sx.game", "work Area.game"]: return "Logic error: game slots cannot be assigned."
            elif right_most.startswith(("Target ", "Choose ", "Fail:")): return "Logic error: condition blocks cannot be assigned."
            elif re.fullmatch(r'UI\((left|right|jump|up|down)\)', right_most):
                direction = re.fullmatch(r'UI\((left|right|jump|up|down)\)', right_most).group(1)
                already_registered = (
                    direction in self.pc_controls
                    or any(o.get("type") == "UI" and o.get("ui_action") == direction for o in self.game_objects)
                )
                if not already_registered:
                    if not self.game_slot_active:
                        return "Please start the game slot first."
                    if not (self.current_obj and self.current_obj.get("type") == "UI" and "ui_action" not in self.current_obj):
                        # Not currently building a fresh UI object (e.g. no prior Create: "UI") -
                        # create one automatically so `s = UI(left)` works as a standalone line.
                        cmd_err = self.execute_game_command('Create: "UI"', line_num)
                        if cmd_err: return f"Statistics error (Line {line_num}): {cmd_err}"
                    cmd_err = self.execute_game_command(right_most, line_num)
                    if cmd_err: return f"Statistics error (Line {line_num}): {cmd_err}"
                    final_val = self.current_obj
                else:
                    # Already registered (mobile button or PC keyboard control): read live press state.
                    final_val = bool(self.live_input_state.get(direction, False))
            elif re.fullmatch(r'GUI\(\s*(?:Button|Text|Label|InputBox|Input|GUI|Image)\s*,\s*Bind\s*=\s*([A-Za-z_]\w*)\s*\)', right_most):
                bound_fn = re.fullmatch(r'GUI\(\s*(?:Button|Text|Label|InputBox|Input|GUI|Image)\s*,\s*Bind\s*=\s*([A-Za-z_]\w*)\s*\)', right_most).group(1)
                final_val = bool(self.gui_clicked_state.get(bound_fn, False))
                self.gui_clicked_state[bound_fn] = False
            elif right_most.startswith(("Create:", "Set:", "Size:", "Color:", "Text:", "Shape:", "Text.Color:", "Text.Size:", "UI(", "Score:", "Score.Color:", "Score.Size:", "Anchored:", "Collision:", "Transparency:", "GUI", "move:")) or re.match(r'^\w+\.(Create:|Size:|Color:|Text:|Shape:|Text\.Color:|Text\.Size:|Set:|Score:|Score\.Color:|Score\.Size:|Anchored:|Collision:|Transparency:)', right_most):
                cmd_err = self.execute_game_command(right_most, line_num)
                if cmd_err: return f"Statistics error (Line {line_num}): {cmd_err}"
                final_val = self.current_obj
            elif "random:" in right_most:
                origin = "random"
                final_val, err = self.resolve_general_value(right_most, allow_raw_string=False)
            elif "coordinat:" in right_most:
                origin = "coordinat"
                final_val, err = self.resolve_general_value(right_most, allow_raw_string=False)
            elif right_most.startswith("Repeat:"): 
                res, err_val = self.evaluate_repeat(right_most)
                if err_val: return err_val
                final_val = res if res is not None else []
                if isinstance(final_val, list) and final_val and all(isinstance(x, int) and not isinstance(x, bool) for x in final_val):
                    origin = "int"
            elif right_most.startswith("free"): final_val, err = self.parse_builtin_functions(right_most)
            elif right_most.startswith("abs"): final_val, err = self.parse_builtin_functions(right_most)
            elif right_most.startswith("round"): final_val, err = self.parse_builtin_functions(right_most)
            elif right_most.startswith("open") or right_most.startswith("zip") or right_most.startswith("think") or right_most.startswith("locals") or right_most.startswith("globals") or right_most.startswith("TYPE"): final_val, err = self.parse_builtin_functions(right_most)
            elif right_most.startswith("write("):
                _, err = self.handle_write(right_most)
                final_val = None
            elif right_most.startswith("work("): final_val, err = self.handle_work_syntax(right_most)
            else:
                fn_match = re.match(r'^(\*?[\w.]+)\((.*)\)$', right_most)
                if fn_match and fn_match.group(1) not in ["int", "fround", "str", "bool", "max", "min", "len", "type", "check", "id", "free", "dict", "list", "tuple", "abs", "round", "open", "zip", "think", "locals", "globals", "TYPE", "setattr", "getattr"]:
                    final_val, err = self.execute_possibly_chained_call(right_most, line_num)
                else: final_val, err = self.resolve_general_value(right_most, allow_raw_string=False)

        if err: return err
        
        for part in assign_targets:
            clean_part = part
            if part.startswith("table."): clean_part = part[6:]
            
            attr_match = re.fullmatch(r'^(\w+)\.(\w+)$', clean_part)
            if attr_match: pass
            elif re.fullmatch(r'^([\w.]+)\[(.*)\]$', clean_part): pass
            elif not re.fullmatch(r'^\*?\w+$', part) or not part.replace('*', '').isidentifier(): return f"'{part}' is not a valid variable name."
            elif part in ILUS_KEYWORDS: return f"Statistics error: '{part}' is a keyword and cannot be used as a variable name!"

        for part in assign_targets:
            clean_part = part
            if part.startswith("table."): clean_part = part[6:]
            
            attr_match = re.fullmatch(r'^(\w+)\.(\w+)$', clean_part)
            if attr_match:
                obj_name, attr_name = attr_match.groups()
                if obj_name in self.variables and isinstance(self.variables[obj_name], dict):
                    if self.variables[obj_name].get("__type__") == "instance":
                        self.variables[obj_name]["attrs"][attr_name] = final_val
                        continue
                    elif self.variables[obj_name].get("__type__") == "game_score":
                        if attr_name == "Value":
                            score_obj = self.variables[obj_name]["obj"]
                            if isinstance(final_val, (int, float)):
                                final_val = max(0, min(int(final_val), score_obj["max_val"]))
                                score_obj["value"] = final_val
                                self.variables[obj_name]["attrs"]["Value"] = final_val
                            else:
                                return "Logic error: Score can only be an integer (int)."
                            continue
                else:
                    return f"Logic error: '{obj_name}' not found or is not a related object."

            idx_match = re.fullmatch(r'^([\w.]+)\[(.*)\]$', clean_part)
            if idx_match:
                l_name = idx_match.group(1)
                idx_str = idx_match.group(2).strip()
                if idx_str in ['"end"', '"begin"', 'end', 'begin', "'end'", "'begin'"]: return "Statistics error: \"end\" or \"begin\" cannot be an index!"
                
                obj, err = self.resolve_general_value(l_name, allow_raw_string=True)
                if err: return err
                if obj is None: return f"Logic error: '{l_name}' not found."
                
                if isinstance(obj, list):
                    idx_val, err_i = self.resolve_general_value(idx_str, allow_raw_string=True)
                    if err_i: return err_i
                    if isinstance(idx_val, (int, float)): idx_val = int(idx_val)
                    elif isinstance(idx_val, str) and idx_val.lstrip('-').isdigit(): idx_val = int(idx_val)
                    else: return f"Logic error: Index must be an integer."
                    
                    if 0 <= idx_val < len(obj) or -len(obj) <= idx_val < 0:
                        obj[idx_val] = final_val
                    else:
                        return f"Logic error: Index not found."
                elif isinstance(obj, dict):
                    if obj.get("__type__") == "instance":
                        cls_name = obj["__class__"]
                        return f"Logic error: class '{cls_name}' does not support index assignment."
                    idx_val, err_i = self.resolve_general_value(idx_str, allow_raw_string=True)
                    if err_i: return err_i
                    obj[idx_val] = final_val
                else: return f"Logic error: '{l_name}' is not a list or dict."
                continue
            else:
                self.variables[part] = final_val
                if origin: self.variable_origins[part] = origin
                elif part in self.variable_origins: del self.variable_origins[part]
        return None

# ===========================================================================
# Ilus IDE - PySide6 GUI shell (Phase 1) - single-file version
# ===========================================================================
#
# Everything above this point (LibraryManager ... IlusInterpreter) is the
# unchanged language engine - pure Python, no PySide6/tkinter dependency.
# Everything below is the PySide6 GUI shell that replaces the old tkinter
# IlusIDE class.
#
# Scope of this phase:
#   - Main window, menu bar, tabbed editor, toolbar (Run / Stats / New Tab / Copy / Paste)
#   - Output/terminal window with blocking free()/input()-style input support
#   - Interpreter Stats window (lines/sec, total lines executed)
#   - IPM terminal (package manager) window
#
# NOT included yet (Phase 2 - the game window / Create: "block" canvas
# rendering, physics loop, mobile UI buttons, GUI(Button/Input/...) widgets,
# Image display): that part uses tkinter's Canvas heavily and needs its own
# careful port to QGraphicsView/QGraphicsScene - deliberately left out so
# Phase 1 can be tested and confirmed working first.
#
# Requirements: pip install PySide6
# Run with:     python ilus_pyside.py
#
# IMPORTANT: this file has NOT been executed/tested in the sandbox that
# produced it (PySide6 isn't available there). Please run it on your machine
# and report back any tracebacks or visual issues so they can be fixed.
# ===========================================================================
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTabWidget, QPlainTextEdit, QFileDialog, QMessageBox,
    QGraphicsView, QGraphicsScene, QLineEdit
)
from PySide6.QtGui import QAction, QTextCursor, QFont, QKeyEvent, QColor, QPen, QBrush, QPixmap, QPainter, QTextCharFormat
from PySide6.QtCore import Qt, QTimer, QObject, Signal, QEvent, QRectF, QThread


try:
    import requests
except ImportError:
    requests = None


SAMPLE_CODE = (
    's = [12,35,52]\n'
    'result = s.startswith(1)\n'
    'b = 27\n'
    'b.replace(0,7)\n'
    'write(b)\n'
    'write(result)\n'
)


# ---------------------------------------------------------------------------
# Thread-safe bridge: the interpreter runs on a background Python thread, and
# Qt requires GUI updates to happen on the main thread. Signals emitted from
# a background thread are automatically queued onto the main thread's event
# loop, so this replaces tkinter's `root.after(0, callback)` pattern.
# ---------------------------------------------------------------------------
class GuiThreadBridge(QObject):
    """General-purpose thread-marshaling bridge for THIRD-PARTY Python
    libraries that Ilus scripts `import`. Ilus scripts run on a background
    thread (so free()/input() can block without freezing the whole app), but
    Qt requires widgets to be created and shown on the main GUI thread.
    Library authors call run_on_gui_thread(func) with a zero-arg function
    that builds and shows their window; this bridge marshals that call onto
    the main thread automatically, the same way request_terminal_input()
    already does for the built-in terminal."""
    run_requested = Signal(object)

    def __init__(self):
        super().__init__()
        self.run_requested.connect(self._execute, Qt.QueuedConnection)

    def _execute(self, func):
        try:
            func()
        except Exception as e:
            import traceback
            print(traceback.format_exc())


# One instance, created on the main thread when the app starts (see main()).
# Library authors do: `from ilus_pyside import run_on_gui_thread`
_GUI_BRIDGE = None


def run_on_gui_thread(func):
    """Call this from an Ilus-imported Python library with a zero-argument
    function that creates and shows a PySide6 window/widget. Example:

        from ilus_pyside import run_on_gui_thread
        from PySide6.QtWidgets import QLabel

        def show_my_window():
            def _build():
                win = QLabel("Hello from my library!")
                win.resize(300, 100)
                win.show()
                win._keep_alive = win  # keep a reference so it isn't GC'd
            run_on_gui_thread(_build)
    """
    if _GUI_BRIDGE is None:
        raise RuntimeError(
            "run_on_gui_thread() was called before the Ilus app started its GUI bridge. "
            "This should only be called from code that runs while the Ilus IDE is open."
        )
    _GUI_BRIDGE.run_requested.emit(func)


def run_on_gui_thread_sync(func, timeout=None):
    """Like run_on_gui_thread(), but BLOCKS the calling (Ilus interpreter)
    thread until func() has actually finished running on the GUI thread,
    then returns func()'s return value - or re-raises whatever exception
    func() raised, in the calling thread.

    Use this whenever the caller needs the GUI-thread work to be done and
    its result ready before continuing - e.g. starting a QAudioSource and
    then immediately reading from it: MicroPhone(True) must not return
    until QAudioSource.start() has actually completed on the GUI thread.

        from ilus_pyside import run_on_gui_thread_sync

        def start_microphone():
            def _build():
                source = QAudioSource(...)
                io_device = source.start()
                return source, io_device
            return run_on_gui_thread_sync(_build)

    This is built entirely on top of the existing run_on_gui_thread() /
    GuiThreadBridge.run_requested signal above - it does not change either
    of them, or how run_on_gui_thread() itself behaves for existing callers.

    `timeout` (seconds, optional): if given and func() doesn't finish in
    time, raises TimeoutError instead of waiting forever (e.g. if the GUI
    thread is itself blocked on something and can never run func()).
    """
    # If we're already ON the GUI thread (e.g. called from a Bind= callback
    # that fired from a GUI event), there's no other thread to marshal to -
    # emitting through the queued signal here would deadlock (this thread
    # would block waiting for an event loop iteration that can't happen
    # because this same thread is the one stuck waiting). Just call it directly.
    if _GUI_BRIDGE is not None and QThread.currentThread() is _GUI_BRIDGE.thread():
        return func()

    done_event = threading.Event()
    outcome = {"result": None, "error": None}

    def _wrapped():
        try:
            outcome["result"] = func()
        except BaseException as e:
            outcome["error"] = e
        finally:
            done_event.set()

    run_on_gui_thread(_wrapped)  # unchanged - same signal, same path

    if not done_event.wait(timeout=timeout):
        raise TimeoutError(
            "run_on_gui_thread_sync() timed out waiting for the GUI thread to run func()."
        )

    if outcome["error"] is not None:
        raise outcome["error"]
    return outcome["result"]


# ---------------------------------------------------------------------------
# Separate-PROCESS bridge, for GUI frameworks that can't share a process with
# Qt at all (Kivy, tkinter, PyGame, ...). run_on_gui_thread()/_sync() above
# solve the "different thread, same process" problem (for PySide6 widgets
# themselves); this solves the harder "different GUI framework entirely"
# problem, where even being on the right THREAD isn't enough because two
# native GUI toolkits fighting over the same process's event loop is what
# actually breaks. Running the other framework in its own OS process sidesteps
# that completely - two processes never conflict with each other's event loop.
# ---------------------------------------------------------------------------
def run_gui_subprocess(script_path, args=None, python_executable=None):
    """Fire-and-forget: launch script_path as a fully independent Python
    process with its own native GUI event loop (Kivy's App().run(),
    tkinter's mainloop(), PyGame's loop, whatever it wants). No
    communication channel is set up - this just starts it running alongside
    Ilus and returns immediately.

    Returns the subprocess.Popen object, in case the caller wants to check
    `.poll()` or call `.terminate()`/`.kill()` on it later.

    Use GuiSubprocessBridge instead if you need to exchange data with the
    subprocess (e.g. read back a result, or send it commands)."""
    cmd = [python_executable or sys.executable, script_path] + list(args or [])
    return subprocess.Popen(cmd)


class GuiSubprocessBridge:
    """Runs a Python script in its own separate OS process (own interpreter,
    own GIL, own event loop) and exchanges JSON messages with it over its
    stdin/stdout - one JSON object per line in each direction. This is how
    you talk to a Kivy/tkinter/etc. window running in a different process
    without needing any of Qt's threading machinery, since the two
    processes are otherwise completely independent.

    Ilus-side usage:
        from ilus_pyside import GuiSubprocessBridge

        def open_kivy_window():
            bridge = GuiSubprocessBridge("kivy_worker.py")
            msg = bridge.receive(timeout=10)   # wait for the subprocess to say it's ready
            return bridge   # keep it around; call bridge.send({...}) / bridge.receive() later

    The subprocess script decides its own protocol - it just needs to print
    one JSON object per line (and flush) to talk back, and read lines from
    stdin to receive commands. Minimal example (kivy_worker.py):

        import sys, json
        from kivy.app import App
        from kivy.uix.label import Label

        class MyApp(App):
            def build(self):
                return Label(text="Hello from Kivy!")

        print(json.dumps({"status": "ready"}), flush=True)
        MyApp().run()
    """

    def __init__(self, script_path, args=None, python_executable=None):
        cmd = [python_executable or sys.executable, script_path] + list(args or [])
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        self._stdout_queue = queue.Queue()
        self._reader_thread = threading.Thread(target=self._read_stdout_loop, daemon=True)
        self._reader_thread.start()

    def _read_stdout_loop(self):
        try:
            for line in self.process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    self._stdout_queue.put(json.loads(line))
                except json.JSONDecodeError:
                    self._stdout_queue.put({"raw": line})
        except Exception:
            pass

    def send(self, obj):
        """Send one JSON-serializable object to the subprocess (one line)."""
        if self.process.stdin is None or self.process.stdin.closed:
            raise RuntimeError("GuiSubprocessBridge: subprocess stdin is closed.")
        self.process.stdin.write(json.dumps(obj) + "\n")
        self.process.stdin.flush()

    def receive(self, timeout=None):
        """Block until the subprocess sends its next JSON line, and return it
        as a Python dict/list/etc. Raises TimeoutError if nothing arrives
        within `timeout` seconds (None = wait forever). If a line isn't
        valid JSON, it comes back as {"raw": "<that line>"} instead of
        raising, so a misbehaving subprocess doesn't crash the caller."""
        try:
            return self._stdout_queue.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(
                "GuiSubprocessBridge: timed out waiting for a message from the subprocess."
            )

    def is_running(self):
        return self.process.poll() is None

    def terminate(self):
        if self.is_running():
            self.process.terminate()

    def kill(self):
        if self.is_running():
            self.process.kill()


# ---------------------------------------------------------------------------
# Single shared "GUI window" resource slot, for when several Ilus GUI
# libraries all want to use ONE window. If everyone who claims it says they
# CAN cooperate/share it, they all get to. The moment anyone (the first
# claimant, or a later one) says it CAN'T share, that claim - and every
# other claim while it's held - is knocked out: only the exclusive holder
# gets the window; everyone else is rejected until it's released.
# ---------------------------------------------------------------------------
class GuiWindowSlot:
    def __init__(self):
        self._lock = threading.Lock()
        self._holders = []  # list of (owner_id, cooperative) currently holding the slot

    def claim(self, owner_id, cooperative=True):
        """Try to claim the shared window slot.
        Returns True if granted (this library may open/use the window),
        False if knocked out (someone already holds it exclusively, or this
        claim itself is exclusive and the slot is already taken)."""
        with self._lock:
            if any(o == owner_id for o, _ in self._holders):
                return True  # already holds it
            if not self._holders:
                self._holders.append((owner_id, cooperative))
                return True
            if cooperative and all(c for _, c in self._holders):
                self._holders.append((owner_id, cooperative))
                return True
            return False  # knocked out

    def release(self, owner_id):
        with self._lock:
            self._holders = [(o, c) for o, c in self._holders if o != owner_id]

    def current_holders(self):
        with self._lock:
            return [o for o, _ in self._holders]


_GUI_WINDOW_SLOT = GuiWindowSlot()


def claim_gui_window(owner_id, cooperative=True):
    """Call this BEFORE actually opening your library's window, with a
    unique name for your library (e.g. "my_flashlight_lib") and whether your
    library can share the single window slot with others (cooperative=True,
    e.g. several PySide6 widgets happily coexisting in one window) or needs
    it to itself (cooperative=False, e.g. a Kivy/tkinter app that owns its
    whole native window).

    Returns True -> go ahead and open/use your window.
    Returns False -> you were knocked out; some other library already holds
    the slot exclusively (or is holding it while you asked for exclusivity).
    Don't open your window in that case - maybe log a message instead.

    Example:
        from ilus_pyside import claim_gui_window, release_gui_window

        def open_my_window():
            if not claim_gui_window("my_flashlight_lib", cooperative=False):
                print("Another GUI library already has the window - skipping.")
                return
            try:
                ...  # actually build and show your window here
            finally:
                pass  # call release_gui_window("my_flashlight_lib") when your window closes
    """
    return _GUI_WINDOW_SLOT.claim(owner_id, cooperative)


def release_gui_window(owner_id):
    """Call this when your library is done with the window (e.g. it closed),
    so other libraries waiting for the slot can claim it."""
    _GUI_WINDOW_SLOT.release(owner_id)


class InterpreterBridge(QObject):
    input_requested = Signal(str)


class TerminalOutputBridge(QObject):
    """Same thread-safety pattern as InterpreterBridge, for background threads
    (e.g. ipm install's network check) that need to write to a terminal
    widget or re-show its prompt. Calling Qt widget methods directly from a
    background thread is undefined behaviour in Qt (unlike tkinter, which
    tolerates it more often) and was causing the terminal to glitch/skip."""
    write_requested = Signal(str)
    reprompt_requested = Signal()


# ---------------------------------------------------------------------------
# A QPlainTextEdit that supports the "type after a prompt, can't edit what
# came before it" terminal behaviour used for both the Output/Terminal window
# and the IPM terminal. This mirrors the tkinter version's use of a named
# mark ("input_start") plus key-event filtering.
# ---------------------------------------------------------------------------
class RestrictedTerminal(QPlainTextEdit):
    line_submitted = Signal(str)

    # Standard + bright ANSI foreground color codes -> QColor names/hex.
    _ANSI_FG_COLORS = {
        30: "#000000", 31: "#e74c3c", 32: "#2ecc71", 33: "#f1c40f",
        34: "#3498db", 35: "#9b59b6", 36: "#1abc9c", 37: "#ecf0f1",
        90: "#7f8c8d", 91: "#ff6b6b", 92: "#6bff6b", 93: "#ffff6b",
        94: "#6b9bff", 95: "#ff6bff", 96: "#6bffff", 97: "#ffffff",
    }
    _ANSI_RE = re.compile(r'\x1b\[([0-9;]*)m')

    def __init__(self, parent=None):
        super().__init__(parent)
        self.input_start_pos = 0
        self.input_active = False
        self._default_format = QTextCharFormat()
        self._default_format.setForeground(QColor("white"))
        self._current_format = QTextCharFormat(self._default_format)

    def lock_input_start(self):
        """Call this right after printing a prompt, to mark where user input begins."""
        self.moveCursor(QTextCursor.End)
        self.input_start_pos = self.textCursor().position()

    def _apply_ansi_codes(self, fmt, codes_str):
        """Update a QTextCharFormat according to one ANSI SGR escape sequence
        (the part between \\x1b[ and m, e.g. "31" or "1;92")."""
        fmt = QTextCharFormat(fmt)
        codes = [c for c in codes_str.split(";") if c != ""] or ["0"]
        for code in codes:
            try:
                n = int(code)
            except ValueError:
                continue
            if n == 0:
                fmt = QTextCharFormat(self._default_format)
            elif n == 1:
                fmt.setFontWeight(QFont.Bold)
            elif n == 22:
                fmt.setFontWeight(QFont.Normal)
            elif n == 39:
                fmt.setForeground(self._default_format.foreground())
            elif n in self._ANSI_FG_COLORS:
                fmt.setForeground(QColor(self._ANSI_FG_COLORS[n]))
        return fmt

    def append_text(self, text):
        """Appends text at the end, honoring ANSI SGR color escape codes
        (e.g. "\\x1b[31mred text\\x1b[0m") if the text contains any - so a
        library can color its own output without needing any special Ilus
        API, just standard ANSI codes. Plain text with no codes is
        unaffected and simply appears in the default (white) color."""
        self.moveCursor(QTextCursor.End)
        cursor = self.textCursor()
        pos = 0
        fmt = self._current_format
        for m in self._ANSI_RE.finditer(text):
            if m.start() > pos:
                cursor.insertText(text[pos:m.start()], fmt)
            fmt = self._apply_ansi_codes(fmt, m.group(1))
            pos = m.end()
        if pos < len(text):
            cursor.insertText(text[pos:], fmt)
        self._current_format = fmt  # carries over to the next append_text() call
        self.moveCursor(QTextCursor.End)
        self.ensureCursorVisible()

    def keyPressEvent(self, event: QKeyEvent):
        if not self.input_active:
            # Read-only while a script is producing output and no input is pending.
            return
        cursor = self.textCursor()
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            text_cursor = self.textCursor()
            text_cursor.setPosition(self.input_start_pos)
            text_cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
            raw_input = text_cursor.selectedText().replace('\u2029', '').replace('\n', '')
            self.append_text("\n")
            self.setReadOnly(True)
            self.input_active = False
            self.line_submitted.emit(raw_input)
            return
        if event.key() == Qt.Key_Backspace and cursor.position() <= self.input_start_pos:
            return  # can't backspace past the prompt
        if cursor.position() < self.input_start_pos:
            self.moveCursor(QTextCursor.End)
        super().keyPressEvent(event)


class GameView(QGraphicsView):
    """QGraphicsView that forwards key/mouse events to plain callback
    functions (set as attributes after construction), mirroring how the
    tkinter version used canvas.bind()/game_win.bind()."""
    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.on_key_down = None
        self.on_key_up = None
        self.on_mouse_press = None
        self.on_mouse_release = None
        self.setFocusPolicy(Qt.StrongFocus)
        self.setRenderHint(QPainter.Antialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def keyPressEvent(self, event):
        if not event.isAutoRepeat() and self.on_key_down: self.on_key_down(event)
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if not event.isAutoRepeat() and self.on_key_up: self.on_key_up(event)
        super().keyReleaseEvent(event)

    def mousePressEvent(self, event):
        if self.on_mouse_press: self.on_mouse_press(event)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self.on_mouse_release: self.on_mouse_release(event)
        super().mouseReleaseEvent(event)


class GameWindow(QMainWindow):
    """QMainWindow subclass with a proper closeEvent override (set via
    `on_close_callback` after construction) - safer than monkey-patching
    closeEvent on a plain QMainWindow instance, which isn't guaranteed to
    hook into Qt's virtual dispatch correctly."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.on_close_callback = None

    def closeEvent(self, event):
        if self.on_close_callback:
            self.on_close_callback()
        event.accept()


# ===========================================================================
# GUI extension bridge - for Python libraries imported into Ilus scripts
# ===========================================================================
#
# WHY THIS EXISTS:
# A user's imported .py library runs on the INTERPRETER's background thread,
# not the Qt/GUI main thread. In tkinter this often "just worked" because
# tkinter is comparatively forgiving about threads. PySide6/Qt is much
# stricter: there can be only ONE QApplication per process (the IDE already
# created it), and Qt widgets may only be created/shown/modified on the main
# thread. A library that does `app = QApplication([]); win = QMainWindow();
# win.show(); app.exec()` from the interpreter thread will fail or hang
# silently - which is exactly why "the window doesn't open".
#
# HOW TO USE (from a Python library file you `import` into an Ilus script):
#
#     from ilus_pyside import ilus_show_gui
#     from PySide6.QtWidgets import QMainWindow, QLabel
#
#     def open_my_window():
#         def build():
#             win = QMainWindow()
#             win.setWindowTitle("My Custom GUI")
#             win.setCentralWidget(QLabel("Hello from a library!"))
#             win.resize(300, 200)
#             win.show()
#             return win          # IMPORTANT: return the window/widget so it
#                                 # gets kept alive (not garbage-collected)
#         ilus_show_gui(build)
#
# `build` runs safely on the Qt main thread no matter which thread calls
# ilus_show_gui() - it is queued there automatically via a Qt signal, the
# same mechanism the IDE itself uses internally for free()/input().
# Do NOT create a QApplication or call .exec() yourself - the IDE already
# owns the single QApplication and its event loop.
# ===========================================================================
class GuiExtensionBridge(QObject):
    run_on_main_thread = Signal(object)

    def __init__(self):
        super().__init__()
        self._kept_alive = []  # prevents Python from garbage-collecting shown windows
        self.run_on_main_thread.connect(self._execute)

    def _execute(self, build_fn):
        try:
            result = build_fn()
            if result is not None:
                self._kept_alive.append(result)
        except Exception as e:
            import traceback
            print(traceback.format_exc())
            if _active_ide_instance and _active_ide_instance.interpreter:
                _active_ide_instance.interpreter.log(f"[GUI extension error]: {e}")


_gui_extension_bridge = None  # set once the IlusIDE is created
_active_ide_instance = None   # set once the IlusIDE is created


def ilus_show_gui(build_fn):
    """Call this from an Ilus-imported Python library to safely show a
    PySide6 window/widget from any thread. `build_fn` takes no arguments,
    must create+show the widget on its own, and should return it (so it
    isn't garbage-collected)."""
    if _gui_extension_bridge is None:
        raise RuntimeError("Ilus GUI bridge isn't ready yet - the IDE must be running first.")
    _gui_extension_bridge.run_on_main_thread.emit(build_fn)


class IlusIDE(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ilus IDE - Full Strict Interpreter (PySide6)")
        self.resize(600, 800)

        self.interpreter = None
        self.terminal_widget = None  # set in _setup_ui() - lives inside the "Console" tab
        self.game_window_opened = False
        self.tabs = {}  # maps QWidget (tab page) -> editor QPlainTextEdit

        self.input_ready_event = threading.Event()
        self.user_input_value = ""
        self.input_active = False

        self.bridge = InterpreterBridge()
        self.bridge.input_requested.connect(self._prepare_terminal_for_input)

        # Make the GUI extension bridge (for Python libraries imported into
        # Ilus scripts) reachable via ilus_show_gui(), regardless of which
        # thread calls it.
        global _gui_extension_bridge, _active_ide_instance
        self.gui_extension_bridge = GuiExtensionBridge()
        _gui_extension_bridge = self.gui_extension_bridge
        _active_ide_instance = self

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_logs)

        self._setup_menu()
        self._setup_ui()

    # -- menu -----------------------------------------------------------
    def _setup_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")

        new_action = QAction("New File (Tab)", self)
        new_action.triggered.connect(lambda: self.new_file())
        file_menu.addAction(new_action)

        open_action = QAction("Open File...", self)
        open_action.triggered.connect(self.open_file)
        file_menu.addAction(open_action)

        save_action = QAction("Save", self)
        save_action.triggered.connect(self.save_file)
        file_menu.addAction(save_action)

        file_menu.addSeparator()
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        terminal_action = QAction("Terminal", self)
        terminal_action.triggered.connect(self.open_terminal)
        menubar.addAction(terminal_action)

    # -- main UI ----------------------------------------------------------
    def _setup_ui(self):
        central = QWidget()
        central.setStyleSheet("background-color: #2c3e50;")
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        title_lbl = QLabel("ILUS INTERPRETER (v4.5.0 - PySide6)")
        title_lbl.setStyleSheet("color: white; font-weight: bold; font-size: 11pt;")
        title_lbl.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_lbl)

        run_toolbar = QVBoxLayout()
        run_btn = QPushButton("▶ RUN CODE")
        run_btn.setStyleSheet("background-color:#e74c3c; color:white; font-weight:bold; padding:6px 10px;")
        run_btn.clicked.connect(self.run_code)
        run_toolbar.addWidget(run_btn)
        main_layout.addLayout(run_toolbar)

        toolbar = QHBoxLayout()
        new_tab_btn = QPushButton("+ New Tab")
        new_tab_btn.setStyleSheet("background-color:#f1c40f; font-weight:bold; padding:4px 10px;")
        new_tab_btn.clicked.connect(lambda: self.new_file())
        copy_btn = QPushButton("📋 Copy")
        copy_btn.setStyleSheet("background-color:#2ecc71; color:white; font-weight:bold; padding:4px 10px;")
        copy_btn.clicked.connect(self.copy_code)
        paste_btn = QPushButton("📋 Paste")
        paste_btn.setStyleSheet("background-color:#3498db; color:white; font-weight:bold; padding:4px 10px;")
        paste_btn.clicked.connect(self.paste_code)
        terminal_btn = QPushButton("🖥 Terminal")
        terminal_btn.setStyleSheet("background-color:#9b59b6; color:white; font-weight:bold; padding:4px 10px;")
        terminal_btn.clicked.connect(self.open_terminal)
        toolbar.addWidget(new_tab_btn)
        toolbar.addWidget(copy_btn)
        toolbar.addWidget(paste_btn)
        toolbar.addWidget(terminal_btn)
        toolbar.addStretch()
        main_layout.addLayout(toolbar)

        self.notebook = QTabWidget()
        self.notebook.setTabsClosable(False)
        main_layout.addWidget(self.notebook)

        self.new_file(title="main.ilus", insert_sample=True)

        # Console tab - like Pydroid3: running code switches you straight to
        # a full console view (not a small side/bottom strip), and you can
        # tap back to an editor tab whenever you like. It's a permanent tab
        # that lives alongside the file tabs, always in the same window.
        console_page = QWidget()
        console_layout = QVBoxLayout(console_page)
        console_layout.setContentsMargins(0, 0, 0, 0)
        self.terminal_widget = RestrictedTerminal()
        self.terminal_widget.setStyleSheet("background-color:black; color:white; font-family: Consolas; font-size: 12pt;")
        self.terminal_widget.setReadOnly(True)
        self.terminal_widget.line_submitted.connect(self._on_terminal_line_submitted)
        console_layout.addWidget(self.terminal_widget)
        self.console_page = console_page
        self.notebook.addTab(console_page, "🖥 Console")

    # -- tabs / editor ------------------------------------------------------
    def get_current_editor(self):
        page = self.notebook.currentWidget()
        return self.tabs.get(page)

    def new_file(self, title="Yeni_File.ilus", insert_sample=False):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        editor = QPlainTextEdit()
        editor.setFont(QFont("Consolas", 15))
        editor.setStyleSheet("background-color:#1e1e1e; color:white;")
        editor.installEventFilter(self)  # for auto-indent on Enter
        layout.addWidget(editor)

        if insert_sample:
            editor.setPlainText(SAMPLE_CODE)

        console_page = getattr(self, "console_page", None)
        if console_page is not None:
            insert_at = self.notebook.indexOf(console_page)
            self.notebook.insertTab(insert_at, page, title)
        else:
            self.notebook.addTab(page, title)
        self.notebook.setCurrentWidget(page)
        self.tabs[page] = editor

    def eventFilter(self, obj, event):
        # Auto-indent: mirrors handle_auto_indent from the tkinter version.
        if event.type() == QEvent.KeyPress and isinstance(obj, QPlainTextEdit):
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                cursor = obj.textCursor()
                block_text = cursor.block().text()
                text_before_cursor = block_text[:cursor.positionInBlock()]
                indent_count = len(text_before_cursor) - len(text_before_cursor.lstrip(" "))
                if block_text.strip().endswith(":"):
                    obj.insertPlainText("\n" + " " * (indent_count + 3))
                    return True
                elif indent_count > 0:
                    obj.insertPlainText("\n" + " " * indent_count)
                    return True
        return super().eventFilter(obj, event)

    def open_file(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Open File", "", "Ilus Files (*.ilus);;All Files (*)")
        if not filepath: return
        with open(filepath, "r", encoding="utf-8") as f:
            code = f.read()
        filename = os.path.basename(filepath)
        self.new_file(title=filename)
        editor = self.get_current_editor()
        if editor:
            editor.setPlainText(code)

    def save_file(self):
        editor = self.get_current_editor()
        if not editor: return
        filepath, _ = QFileDialog.getSaveFileName(self, "Save File", "", "Ilus Files (*.ilus);;All Files (*)")
        if not filepath: return
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(editor.toPlainText())
        filename = os.path.basename(filepath)
        idx = self.notebook.currentIndex()
        self.notebook.setTabText(idx, filename)
        QMessageBox.information(self, "Success", "File saved successfully!")

    def copy_code(self):
        editor = self.get_current_editor()
        if editor:
            QApplication.clipboard().setText(editor.toPlainText().strip())

    def paste_code(self):
        editor = self.get_current_editor()
        if editor:
            editor.insertPlainText(QApplication.clipboard().text())

    # -- output dock / blocking terminal input -----------------------
    def open_output_window(self):
        self.terminal_widget.setReadOnly(True)
        self.terminal_widget.clear()
        self.notebook.setCurrentWidget(self.console_page)

    def request_terminal_input(self, prompt):
        """Called from the INTERPRETER's background thread (not the GUI thread)."""
        self.bridge.input_requested.emit(prompt or "")
        self.input_ready_event.clear()
        self.input_ready_event.wait()
        return self.user_input_value

    def _prepare_terminal_for_input(self, prompt):
        """Runs on the GUI thread (queued via the Qt signal above)."""
        self.notebook.setCurrentWidget(self.console_page)
        term = self.terminal_widget
        term.setReadOnly(False)
        existing = term.toPlainText()
        text_to_insert = (f"\n{prompt} " if prompt else "\n") if existing.strip() else (f"{prompt} " if prompt else "")
        term.append_text(text_to_insert)
        term.lock_input_start()
        term.input_active = True
        term.setFocus()

    def _on_terminal_line_submitted(self, raw_input):
        self.user_input_value = raw_input
        self.input_ready_event.set()

    # -- run flow ---------------------------------------------------------
    def run_code(self):
        editor = self.get_current_editor()
        if not editor: return

        if self.interpreter: self.interpreter.should_stop = True
        self.input_ready_event.set()  # wake up any old run still blocked waiting for input
        self.poll_timer.stop()

        self.open_output_window()
        self._last_log_count = 0

        # A fresh Event for this run, so a slow-to-exit old thread can never
        # get mixed up with this run's input flow.
        self.input_ready_event = threading.Event()

        self.interpreter = IlusInterpreter(request_input_callback=self.request_terminal_input)
        code = editor.toPlainText()
        self.game_window_opened = False

        interpreter_thread = threading.Thread(target=self.interpreter.run, args=(code,), daemon=True)
        interpreter_thread.start()
        self.poll_timer.start(100)

    def poll_logs(self):
        if self.terminal_widget is None:
            self.poll_timer.stop()
            return
        current_logs = list(self.interpreter.logs) if self.interpreter and hasattr(self.interpreter, "logs") else []
        if not self.terminal_widget.input_active:
            self.terminal_widget.setReadOnly(True)
            # Only append what's NEW since the last poll, instead of replacing
            # the whole document every 100ms (that was resetting scroll/cursor
            # position and causing visible flicker/jumpiness).
            last_count = getattr(self, "_last_log_count", 0)
            if len(current_logs) < last_count:
                # Logs were reset (e.g. a fresh run) - start over.
                self.terminal_widget.clear()
                last_count = 0
            new_lines = current_logs[last_count:]
            if new_lines:
                prefix = "\n" if last_count > 0 else ""
                self.terminal_widget.append_text(prefix + "\n".join(new_lines))
            self._last_log_count = len(current_logs)

        if self.interpreter.game_slot_active and not self.game_window_opened and not getattr(self.interpreter, "should_end_game", False):
            if not any("Statistics error" in log or "High-level permission error" in log or "Logic error" in log for log in current_logs):
                self.game_window_opened = True
                self.open_game_window(self.interpreter.game_objects, self.interpreter.pc_controls)

    # -- game window (Phase 2: ported from tkinter Canvas to QGraphicsView) --
    def open_game_window(self, objects, pc_controls):
        win_w, win_h = 400, 500
        game_win = GameWindow(self)
        game_win.setAttribute(Qt.WA_DeleteOnClose, False)
        game_win.setWindowTitle("Ilus Game Area")
        game_win.resize(win_w, win_h)
        # Keep the game window above the output window, like the tkinter "-topmost" version did.
        game_win.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.game_window = game_win  # keep alive (avoid garbage collection)

        scene = QGraphicsScene(0, 0, win_w, win_h)
        scene.setBackgroundBrush(QBrush(QColor("#f5f6fa")))
        view = GameView(scene)
        game_win.setCentralWidget(view)

        keys_pressed = set()
        mobile_keys_pressed = set()
        fired_keys = set()

        def fire_bound_function(fn_name, event_val=True):
            if self.interpreter and fn_name in self.interpreter.functions:
                params = self.interpreter.functions[fn_name].get("params", [])
                call_args = [event_val] if len(params) >= 1 else []
                _, call_err = self.interpreter.execute_function_with_values(fn_name, call_args)
                if call_err:
                    self.interpreter.log(call_err)
                render_objects()

        # Qt key constant -> action name (same actions as UI(left)/UI(right)/UI(up)/UI(down)/UI(jump))
        _KEY_TO_ACTION = {
            Qt.Key_Left: "left", Qt.Key_Right: "right",
            Qt.Key_Up: "up", Qt.Key_Down: "down", Qt.Key_Space: "jump",
        }

        def key_down(event):
            key = event.key()
            if key == Qt.Key_Left and "left" in pc_controls: keys_pressed.add("Left")
            elif key == Qt.Key_Right and "right" in pc_controls: keys_pressed.add("Right")
            elif key == Qt.Key_Up and "up" in pc_controls: keys_pressed.add("Up")
            elif key == Qt.Key_Down and "down" in pc_controls: keys_pressed.add("Down")
            elif key == Qt.Key_Space and "jump" in pc_controls: keys_pressed.add("space")

            action = _KEY_TO_ACTION.get(key)
            if action and action in pc_controls:
                if self.interpreter: self.interpreter.live_input_state[action] = True
                if action not in fired_keys:
                    fired_keys.add(action)
                    bound_fn = self.interpreter.pc_control_binds.get(action) if self.interpreter else None
                    if bound_fn:
                        fire_bound_function(bound_fn)

        def key_up(event):
            key = event.key()
            keysym_set = {Qt.Key_Left: "Left", Qt.Key_Right: "Right", Qt.Key_Up: "Up",
                          Qt.Key_Down: "Down", Qt.Key_Space: "space"}
            if key in keysym_set:
                keys_pressed.discard(keysym_set[key])
            action = _KEY_TO_ACTION.get(key)
            if action:
                fired_keys.discard(action)
                if self.interpreter: self.interpreter.live_input_state[action] = False

        def on_click(event):
            view_pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            pos = view.mapToScene(view_pos)
            ex, ey = pos.x(), pos.y()
            for obj in objects:
                if obj["type"] == "UI" and "ui_action" in obj:
                    x, y, w, h = obj["x"], obj["y"], obj["size"][0], obj["size"][1]
                    if x <= ex <= x + w and y <= ey <= y + h:
                        mobile_keys_pressed.add(obj["ui_action"])
                        if self.interpreter: self.interpreter.live_input_state[obj["ui_action"]] = True
                        if obj.get("ui_bound_event"):
                            fire_bound_function(obj["ui_bound_event"], (ex, ey))
                if obj.get("gui_bound_event") and obj.get("gui_type") not in ("Input", "InputBox"):
                    x, y, w, h = obj["x"], obj["y"], obj["size"][0], obj["size"][1]
                    if x <= ex <= x + w and y <= ey <= y + h:
                        fn_name = obj["gui_bound_event"]
                        if self.interpreter:
                            self.interpreter.gui_clicked_state[fn_name] = True
                        if self.interpreter and fn_name in self.interpreter.functions:
                            params = self.interpreter.functions[fn_name].get("params", [])
                            call_args = [(ex, ey)] if len(params) >= 1 else []
                            _, click_err = self.interpreter.execute_function_with_values(fn_name, call_args)
                            if click_err:
                                self.interpreter.log(click_err)
                            render_objects()

        def on_release(event):
            if self.interpreter:
                for action in mobile_keys_pressed:
                    self.interpreter.live_input_state[action] = False
            mobile_keys_pressed.clear()

        def _safe_event(fn, label):
            def wrapper(event):
                try:
                    fn(event)
                except Exception as e:
                    import traceback
                    tb = traceback.format_exc()
                    print(tb)  # visible in the console/terminal running the app
                    if self.interpreter:
                        self.interpreter.log(f"[Game window {label} handler error]: {e}")
            return wrapper

        view.on_key_down = _safe_event(key_down, "key_down")
        view.on_key_up = _safe_event(key_up, "key_up")
        view.on_mouse_press = _safe_event(on_click, "mouse_press")
        view.on_mouse_release = _safe_event(on_release, "mouse_release")

        def blend_color(hex_or_name, alpha, bg="#f5f6fa"):
            color_map = {
                "red": (255, 0, 0), "green": (0, 128, 0), "blue": (0, 0, 255),
                "black": (0, 0, 0), "white": (255, 255, 255), "yellow": (255, 255, 0),
                "purple": (128, 0, 128), "orange": (255, 165, 0), "pink": (255, 192, 203),
                "brown": (165, 42, 42), "gray": (128, 128, 128), "cyan": (0, 255, 255),
                "magenta": (255, 0, 255), "unknow": (0, 0, 0)
            }
            if hex_or_name in color_map: r, g, b = color_map[hex_or_name]
            else:
                try:
                    hex_val = hex_or_name.lstrip('#')
                    if len(hex_val) == 6: r, g, b = tuple(int(hex_val[i:i+2], 16) for i in (0, 2, 4))
                    else: r, g, b = 0, 0, 0
                except Exception: r, g, b = 0, 0, 0

            br, bg_g, bb = 245, 246, 250
            nr = int(r * (1 - alpha) + br * alpha)
            ng = int(g * (1 - alpha) + bg_g * alpha)
            nb = int(b * (1 - alpha) + bb * alpha)
            return f"#{nr:02x}{ng:02x}{nb:02x}"

        def submit_input(obj):
            line_edit = obj.get("_input_widget")
            if line_edit is None: return
            self.interpreter.pending_input_value = line_edit.text()
            self.interpreter.active_input_object = obj
            fn_name = obj.get("gui_bound_event")
            if fn_name:
                fire_bound_function(fn_name)
            self.interpreter.pending_input_value = None
            self.interpreter.active_input_object = None

        def render_objects():
            # Remove only the "static" items (shapes/text/images) drawn last
            # frame - Input/InputBox widgets are kept alive across frames so
            # the user's typed text and focus state aren't wiped every tick.
            for item in getattr(render_objects, "_static_items", []):
                try: scene.removeItem(item)
                except Exception: pass
            render_objects._static_items = []

            gui_mode = any(o.get("gui_type") == "GUI" for o in objects)
            area_obj = next((o for o in objects if o["type"] == "area"), None)
            area_offset = (area_obj["x"], area_obj["y"]) if (gui_mode and area_obj) else (0, 0)
            gui_screen_obj = next((o for o in objects if o.get("gui_type") == "GUI"), None)
            if gui_screen_obj:
                bg_col = gui_screen_obj.get("color") or "#f5f6fa"
                scene.setBackgroundBrush(QBrush(QColor(bg_col)))

            for obj in sorted(objects, key=lambda o: 0 if o["type"] == "area" else 1):
                obj_type = obj["type"]

                if gui_mode and obj_type in ("block", "circle") and not obj.get("gui_type"):
                    if not area_obj: continue

                if obj_type == "score":
                    txt_item = scene.addSimpleText(str(obj['value']), QFont("Arial", obj.get("text_size", (16, 16))[0], QFont.Bold))
                    txt_item.setBrush(QBrush(QColor(obj.get("text_color", "black"))))
                    txt_item.setPos(obj["x"], obj["y"])
                    render_objects._static_items.append(txt_item)
                    continue

                w, h = obj["size"]
                color = obj["color"]
                x, y = obj["x"], obj["y"]
                if gui_mode and area_obj and obj_type in ("block", "circle"):
                    x, y = area_offset[0] + x, area_offset[1] + y

                t_val = obj.get("transparency", 0.0)
                if t_val > 0.0:
                    if t_val >= 1.0: continue
                    color = blend_color(color, t_val)

                if obj.get("gui_type") in ("Input", "InputBox"):
                    is_box = obj.get("gui_type") == "InputBox"
                    entry_font_size = (obj.get("text_size") or (11, 11))[0]
                    entry_fg = obj.get("text_color") or "black"
                    proxy = obj.get("_input_proxy")
                    if proxy is None:
                        line_edit = QLineEdit()
                        line_edit.setFrame(is_box)
                        if obj.get("text"): line_edit.setText(obj["text"])
                        line_edit.returnPressed.connect(lambda o=obj: submit_input(o))
                        proxy = scene.addWidget(line_edit)
                        obj["_input_widget"] = line_edit
                        obj["_input_proxy"] = proxy
                    else:
                        line_edit = obj["_input_widget"]
                    line_edit.setFont(QFont("Arial", entry_font_size))
                    line_edit.setStyleSheet(f"color:{entry_fg};")
                    box_bg = obj.get("color") if (obj.get("color") and (obj["color"] in VALID_COLORS or QColor(obj["color"]).isValid())) else "white"
                    line_edit.setStyleSheet(f"color:{entry_fg}; background-color:{box_bg};")
                    if obj.get("_input_prompt") and line_edit.placeholderText() != obj["_input_prompt"]:
                        line_edit.setPlaceholderText(obj["_input_prompt"])
                    proxy.resize(max(w - 6, 10), max(h - 6, 10))
                    proxy.setPos(x + 3, y + 3)
                    if is_box:
                        rect_item = scene.addRect(x - 4, y - 4, w + 8, h + 8, QPen(QColor("#555555"), 2), QBrush(QColor(box_bg)))
                        rect_item.setZValue(-1)
                        render_objects._static_items.append(rect_item)
                        if obj.get("_input_prompt"):
                            lbl = scene.addSimpleText(obj["_input_prompt"], QFont("Arial", (obj.get("text_size") or (10, 10))[0]))
                            lbl.setBrush(QBrush(QColor(obj.get("text_color") or "black")))
                            lbl_rect = lbl.boundingRect()
                            lbl.setPos(x + w / 2 - lbl_rect.width() / 2, y - 14 - lbl_rect.height())
                            render_objects._static_items.append(lbl)
                    continue

                if obj.get("gui_type") == "Image" and obj.get("image_path"):
                    pixmap_ref = obj.get("_image_pixmap")
                    if pixmap_ref is None:
                        try:
                            pixmap = QPixmap(obj["image_path"])
                            if pixmap.isNull():
                                raise ValueError("could not load image")
                            if w > 0 and h > 0:
                                pixmap = pixmap.scaled(int(w), int(h))
                            obj["_image_pixmap"] = pixmap
                            pixmap_ref = pixmap
                        except Exception as e:
                            if self.interpreter: self.interpreter.log(f"GUI Image error: could not load '{obj.get('image_path')}': {e}")
                            obj["_image_pixmap"] = False
                            pixmap_ref = False
                    if pixmap_ref:
                        img_item = scene.addPixmap(pixmap_ref)
                        img_item.setPos(x, y)
                        render_objects._static_items.append(img_item)
                    continue

                if not isinstance(color, str) or not (color in VALID_COLORS or QColor(color).isValid()):
                    color = "black"

                if obj.get("gui_type") == "GUI":
                    pass  # its Color: already applied as the scene background above
                elif obj.get("text_only"):
                    pass  # floating text with no background box
                elif obj_type in ("block", "area", "UI"):
                    item = scene.addRect(x, y, w, h, QPen(Qt.NoPen), QBrush(QColor(color)))
                    render_objects._static_items.append(item)
                elif obj_type == "circle":
                    item = scene.addEllipse(x, y, w, h, QPen(Qt.NoPen), QBrush(QColor(color)))
                    render_objects._static_items.append(item)

                if obj.get("text"):
                    t_color = obj.get("text_color") if obj.get("text_color") else "white"
                    if t_color == "unknow": t_color = "white"
                    t_size = obj.get("text_size")
                    font_size = t_size[0] if t_size else 11
                    txt_item = scene.addSimpleText(obj["text"], QFont("Arial", font_size, QFont.Bold))
                    txt_item.setBrush(QBrush(QColor(t_color)))
                    trect = txt_item.boundingRect()
                    txt_item.setPos(x + w / 2 - trect.width() / 2, y + h / 2 - trect.height() / 2)
                    render_objects._static_items.append(txt_item)

        def physics_tick():
            if not game_win.isVisible(): return
            if self.interpreter and getattr(self.interpreter, "should_end_game", False):
                self.interpreter.should_end_game = False
                self.interpreter.game_slot_active = False
                self.game_window_opened = False
                physics_timer.stop()
                game_win.close()
                return

            default_floor_y = view.viewport().height() if view.viewport().height() > 10 else win_h
            moved = False

            for obj in objects:
                if obj["type"] in ("UI", "score"): continue

                is_anchored = obj.get("anchored", False if obj["type"] in ("block", "circle") else True)
                can_collide = obj.get("can_collide", True)

                if obj.get("is_player") and not is_anchored:
                    step_x = abs(obj.get("move_dx", 5))
                    step_y = abs(obj.get("move_dy", 5))
                    new_x = obj["x"]
                    if "Left" in keys_pressed or "left" in mobile_keys_pressed: new_x -= step_x
                    if "Right" in keys_pressed or "right" in mobile_keys_pressed: new_x += step_x

                    can_move_x = True
                    if can_collide:
                        for other in objects:
                            if other is not obj and other["type"] not in ("UI", "score") and other.get("can_collide", True):
                                w, h = obj["size"]
                                ow, oh = other["size"]
                                if (new_x < other["x"] + ow and new_x + w > other["x"] and
                                        obj["y"] < other["y"] + oh and obj["y"] + h > other["y"]):
                                    can_move_x = False
                                    break
                    if can_move_x and obj["x"] != new_x:
                        obj["x"] = new_x
                        moved = True

                    if "Up" in keys_pressed or "up" in mobile_keys_pressed:
                        obj["y"] -= step_y; moved = True
                    if "Down" in keys_pressed or "down" in mobile_keys_pressed:
                        obj["y"] += step_y; moved = True

                    if ("space" in keys_pressed or "jump" in mobile_keys_pressed) and obj.get("can_jump", False):
                        obj["y_vel"] = -abs(obj.get("move_dy", 13)) if obj.get("is_player") else -13
                        obj["can_jump"] = False
                        moved = True

                if not is_anchored:
                    w, h = obj["size"]
                    floor_y = default_floor_y
                    inside_area_floor = None

                    if can_collide:
                        for other_obj in objects:
                            if other_obj is not obj and other_obj["type"] not in ("UI", "score") and other_obj.get("can_collide", True):
                                ax, ay = other_obj["x"], other_obj["y"]
                                aw, ah = other_obj["size"]
                                if (obj["x"] + w > ax) and (obj["x"] < ax + aw):
                                    if obj["y"] + h <= ay + 15:
                                        if floor_y > ay:
                                            floor_y = ay
                                    elif obj["y"] >= ay and obj["y"] + h <= ay + ah + 15:
                                        if inside_area_floor is None or (ay + ah) < inside_area_floor:
                                            inside_area_floor = ay + ah

                    if inside_area_floor is not None and floor_y == default_floor_y:
                        floor_y = inside_area_floor

                    if "y_vel" not in obj: obj["y_vel"] = 0
                    obj["y_vel"] += 1
                    if obj["y_vel"] > 10: obj["y_vel"] = 10
                    obj["y"] += obj["y_vel"]

                    if obj["y"] + h >= floor_y:
                        obj["y"] = floor_y - h
                        obj["y_vel"] = 0
                        obj["can_jump"] = True

                    moved = True

            if moved or any(o["type"] == "score" for o in objects):
                render_objects()

        def on_game_window_close():
            physics_timer.stop()
            if self.interpreter:
                self.interpreter.game_slot_active = False
                self.interpreter.should_end_game = False
            self.game_window_opened = False

        game_win.on_close_callback = on_game_window_close

        physics_timer = QTimer(game_win)
        def _safe_physics_tick():
            try:
                physics_tick()
            except Exception as e:
                import traceback
                print(traceback.format_exc())
                if self.interpreter:
                    self.interpreter.log(f"[Game window physics loop error]: {e}")
                physics_timer.stop()

        physics_timer.timeout.connect(_safe_physics_tick)
        physics_timer.start(25)

        render_objects()
        game_win.show()
        game_win.raise_()
        game_win.activateWindow()
        view.setFocus()

    # -- IPM terminal -------------------------------------------------------
    def open_terminal(self):
        # Reuse the existing terminal window if it's still open, instead of
        # creating (and possibly garbage-collecting) a new one each time.
        if getattr(self, "ipm_terminal_window", None) is not None:
            self.ipm_terminal_window.show()
            self.ipm_terminal_window.raise_()
            self.ipm_terminal_window.activateWindow()
            return

        win = QMainWindow(self)
        win.setAttribute(Qt.WA_DeleteOnClose, False)
        win.setWindowTitle("Ilus IPM Terminal")
        win.resize(550, 450)

        central = QWidget()
        win.setCentralWidget(central)
        outer_layout = QVBoxLayout(central)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        top_bar = QWidget()
        top_bar.setStyleSheet("background-color: #2c3e50;")
        top_layout = QHBoxLayout(top_bar)
        exit_btn = QPushButton("✕ Çıxış (Exit)")
        exit_btn.setStyleSheet("background-color:#e74c3c; color:white; font-weight:bold; padding:8px 14px;")
        exit_btn.clicked.connect(win.close)
        title_lbl = QLabel("Ilus IPM Terminal")
        title_lbl.setStyleSheet("color:white; font-weight:bold; font-size:11pt;")
        top_layout.addWidget(exit_btn)
        top_layout.addWidget(title_lbl)
        top_layout.addStretch()
        outer_layout.addWidget(top_bar)

        term = RestrictedTerminal()
        term.setStyleSheet("background-color:black; color:#00ff00; font-family: Consolas; font-size: 11pt;")
        outer_layout.addWidget(term)
        # IMPORTANT: keep a strong reference on self, otherwise Python may
        # garbage-collect this window right after this method returns and it
        # never appears (or flashes and disappears) - a common PySide6 pitfall
        # with locally-created top-level windows.
        self.ipm_terminal_window = win

        term_bridge = TerminalOutputBridge()
        self.ipm_terminal_window._term_bridge = term_bridge  # keep alive with the window
        prompt_text = "C:\\Ilus\\Projects> "

        def term_write(text):
            term.append_text(text + "\n")

        def get_size_str(path):
            if os.path.exists(path):
                size = os.path.getsize(path)
                return f"{size} B" if size < 1024 else f"{size/1024:.2f} KB"
            return "Unknown"

        def publish_to_firebase(pkg, size_str):
            def task():
                if requests is None: return
                try:
                    project_id = "ilusdata"
                    collection = "packages"
                    doc_id = pkg
                    url = f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)/documents/{collection}/{doc_id}"
                    data = {"fields": {
                        "kitabxana": {"stringValue": pkg},
                        "olcu": {"stringValue": str(size_str)},
                        "timestamp": {"stringValue": str(time.time())},
                    }}
                    requests.patch(url, json=data, timeout=5)
                except Exception:
                    pass
            threading.Thread(target=task, daemon=True).start()

        term_write("Welcome to the Ilus Package Manager (IPM) Terminal.")
        term_write("Commands:")
        term_write("- ipm install <package_name>")
        term_write("- ipm list")
        term_write("- ipm <package_name> package")
        term_write("---------------------------------------------------")
        term.append_text(prompt_text)
        term.lock_input_start()
        term.input_active = True
        term.setReadOnly(False)

        def handle_cmd(cmd):
            if cmd == "ipm list":
                if IlusInterpreter.installed_packages:
                    term_write("List of installed packages:")
                    for p_name, p_cpu in IlusInterpreter.installed_packages.items():
                        size_str = get_size_str(p_name + ".ilus") if "lusCPU" in p_cpu else get_size_str(p_name + ".py")
                        if p_name == "ilusaztar": size_str = "Daxili"
                        term_write(f"- {p_name} ({p_cpu}) [Size: {size_str}]")
                else:
                    term_write("No packages installed on the system.")

            elif cmd.startswith("ipm ") and cmd.endswith(" package"):
                parts = cmd.split(" ")
                if len(parts) == 3:
                    pkg = parts[1].strip()
                    if pkg == "ilusaztar" or os.path.exists(pkg + ".py"):
                        term_write(f"ipm [{pkg}] is package")
                        term_write("CPU_NAME = PyCPU")
                        size_val = get_size_str(pkg + ".py") if pkg != "ilusaztar" else "Daxili"
                        term_write(f"File size = {size_val}")
                        term_write("Writing to Firebase server...")
                        publish_to_firebase(pkg, size_val)
                    elif os.path.exists(pkg + ".ilus"):
                        term_write(f"ipm [{pkg}] is package")
                        term_write("CPU_NAME = IlusCPU")
                        size_str = get_size_str(pkg + ".ilus")
                        term_write(f"File size = {size_str}")
                        term_write("Writing to Firebase server...")
                        publish_to_firebase(pkg, size_str)
                    else:
                        term_write(f"Error: file '{pkg}' not found! Packages without files cannot be installed!")
                else:
                    term_write("Unknown command format.")

            elif cmd.startswith("ipm install "):
                pkg = cmd.split("ipm install ")[1].strip()
                if pkg in IlusInterpreter.installed_packages:
                    cpu_type = IlusInterpreter.installed_packages[pkg]
                    term_write(f"{cpu_type}: ipm [{pkg}] was installed")
                    _reprompt()
                    return

                def check_install():
                    if pkg == "ilusaztar":
                        time.sleep(0.6)
                        term_bridge.write_requested.emit(f"PyCPU: ipm [{pkg}] successful. File size: Built-in")
                        IlusInterpreter.installed_packages[pkg] = "PyCPU"
                        term_bridge.reprompt_requested.emit()
                        return
                    if requests is None:
                        term_bridge.write_requested.emit("Error: 'requests' library not available.")
                        term_bridge.reprompt_requested.emit()
                        return
                    try:
                        project_id = "ilusdata"
                        collection = "packages"
                        url = f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)/documents/{collection}/{pkg}"
                        res = requests.get(url, timeout=5)
                        if res.status_code != 200:
                            term_bridge.write_requested.emit(f"Error: package '{pkg}' is not registered! Files must first be converted to a package (ipm {pkg} package).")
                            term_bridge.reprompt_requested.emit()
                            return
                    except Exception:
                        term_bridge.write_requested.emit("Error: could not connect to the (Firebase) server!")
                        term_bridge.reprompt_requested.emit()
                        return

                    if os.path.exists(pkg + ".ilus"):
                        time.sleep(0.6)
                        size_str = get_size_str(pkg + ".ilus")
                        term_bridge.write_requested.emit(f"IlusCPU: ipm [{pkg}] successful. File size: {size_str}")
                        IlusInterpreter.installed_packages[pkg] = "IlusCPU"
                    elif os.path.exists(pkg + ".py"):
                        time.sleep(0.6)
                        size_str = get_size_str(pkg + ".py")
                        term_bridge.write_requested.emit(f"PyCPU: ipm [{pkg}] successful. File size: {size_str}")
                        IlusInterpreter.installed_packages[pkg] = "PyCPU"
                    else:
                        term_bridge.write_requested.emit(f"Error: package '{pkg}' exists on the server, but the file ({pkg}.ilus or .py) was not found locally!")
                    term_bridge.reprompt_requested.emit()

                term_write(f"System: checking ipm found [{pkg}]...")
                threading.Thread(target=check_install, daemon=True).start()
                return

            elif cmd:
                term_write(f"Unknown command: '{cmd}'")

            _reprompt()

        def _reprompt():
            term.append_text(prompt_text)
            term.lock_input_start()
            term.input_active = True
            term.setReadOnly(False)

        term_bridge.write_requested.connect(term_write)
        term_bridge.reprompt_requested.connect(_reprompt)
        term.line_submitted.connect(handle_cmd)
        win.show()
        term.setFocus()


def main():
    global _GUI_BRIDGE

    # Fall back to software OpenGL rendering if the system's real GPU
    # driver/context is broken (common in VMs, WSL without GPU passthrough,
    # or missing/old drivers) - this is what "QRhiGles2: Failed to make
    # context current" / "Failed to create QRhi" mean. Regular QWidget UI
    # (the editor, tabs, output window) doesn't need this, but Qt3D/QtQuick3D
    # and similar GPU-accelerated modules do. Set ILUS_FORCE_SOFTWARE_GL=0
    # in the environment to disable this if your GPU/drivers are fine and
    # you want real hardware acceleration.
    if os.environ.get("ILUS_FORCE_SOFTWARE_GL", "1") != "0":
        try:
            QApplication.setAttribute(Qt.AA_UseSoftwareOpenGL, True)
        except Exception:
            pass  # older/newer PySide6 versions may name this differently - safe to skip

    app = QApplication(sys.argv)
    _GUI_BRIDGE = GuiThreadBridge()  # created here = lives on the main thread
    ide = IlusIDE()
    ide.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
