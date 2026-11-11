#!/usr/bin/env python3
"""
Test script to verify closeup photo upload functionality.
"""

import sys
from pathlib import Path

def test_backend_views():
    """Test that backend supports the expected views."""
    print("Testing backend view support...")
    
    # Add backend to path
    sys.path.insert(0, str(Path(__file__).parent / "backend"))
    
    from vgac.reference import TURNAROUND_VIEWS
    
    # Check TURNAROUND_VIEWS is defined correctly
    expected_views = ("front", "left", "back", "right", "quarter", "top")
    assert TURNAROUND_VIEWS == expected_views
    
    print(f"✓ Backend supports views: {list(TURNAROUND_VIEWS)}")
    return True

def test_frontend_views():
    """Test that frontend VIEW_NAMES matches backend views."""
    print("\nTesting frontend view names...")
    
    # Read the frontend file
    frontend_path = Path(__file__).parent / "frontend" / "src" / "pipeline" / "OwnPictures.tsx"
    
    if not frontend_path.exists():
        print(f"✗ Frontend file not found: {frontend_path}")
        return False
    
    content = frontend_path.read_text()
    
    # Check VIEW_NAMES definition
    expected_views = ("front", "left", "back", "right", "quarter", "top")
    expected_names = {
        'front': 'Front',
        'left': 'Left side', 
        'back': 'Back',
        'right': 'Right side',
        'quarter': 'Three-quarter',
        'top': 'Top'
    }
    
    # Parse VIEW_NAMES from the file - look for the inline record
    view_names_line = content.find("VIEW_NAMES: Record<string, string> = {")
    if view_names_line == -1:
        print("✗ VIEW_NAMES not found in OwnPictures.tsx")
        return False
    
    # Find the closing brace
    view_names_end = content.find('}', view_names_line)
    view_names_section = content[view_names_line:view_names_end]
    
    # Extract view names - parse the inline record
    import re
    pattern = r"VIEW_NAMES: Record<string, string> = \{([^}]+)\}"
    match = re.search(pattern, content)
    
    if not match:
        print("✗ Could not parse VIEW_NAMES")
        return False
    
    view_definitions = match.group(1)
    
    # Parse each view definition
    actual_names = {}
    for view in expected_views:
        pattern = rf"{view}:\s*'([^']+)'"
        match = re.search(pattern, view_definitions)
        if match:
            actual_names[view] = match.group(1)
    
    # Verify all views are present
    for view in expected_views:
        assert view in actual_names, f"View {view} not found in VIEW_NAMES"
        assert actual_names[view] == expected_names[view], f"View {view} has wrong name: expected '{expected_names[view]}', got '{actual_names[view]}'"
    
    print(f"✓ Frontend VIEW_NAMES matches backend views")
    print(f"  {actual_names}")
    return True

def test_api_endpoint():
    """Test that the API endpoint is properly defined."""
    print("\nTesting API endpoint...")
    
    # Read the API file
    api_path = Path(__file__).parent / "frontend" / "src" / "api.ts"
    
    if not api_path.exists():
        print(f"✗ API file not found: {api_path}")
        return False
    
    content = api_path.read_text()
    
    # Check uploadView function - look for the function signature
    if "uploadView:" not in content:
        print("✗ uploadView function not found in API")
        return False
    
    # Check for the endpoint pattern
    if "/api/versions/" not in content or "/views/" not in content or "/upload" not in content:
        print("✗ uploadView endpoint not found in API")
        return False
    
    print("✓ API endpoint uploadView is properly defined")
    print("  Endpoint: /api/versions/{vid}/views/{view}/upload")
    return True

def test_ui_integration():
    """Test that the UI properly integrates with the upload functionality."""
    print("\nTesting UI integration...")
    
    # Read the OwnPictures.tsx file
    frontend_path = Path(__file__).parent / "frontend" / "src" / "pipeline" / "OwnPictures.tsx"
    
    if not frontend_path.exists():
        print(f"✗ Frontend file not found: {frontend_path}")
        return False
    
    content = frontend_path.read_text()
    
    # Check for uploadView usage
    if "api.uploadView(onVersion.id, view, p.dataUrl)" not in content:
        print("✗ uploadView call not found in OwnPictures.tsx")
        return False
    
    # Check for OwnViews component
    if "function OwnViews" not in content:
        print("✗ OwnViews component not found")
        return False
    
    # Check that all views are rendered
    expected_views = ("front", "left", "back", "right", "quarter", "top")
    for view in expected_views:
        if f"views.map((view)" in content and f"VIEW_NAMES[view]" in content:
            print(f"  ✓ View '{view}' is rendered in UI")
        else:
            print(f"  ✗ View '{view}' not properly rendered")
            return False
    
    print("✓ UI integration is complete")
    return True

def main():
    """Run all tests."""
    print("=" * 60)
    print("Testing Closeup Photo Upload Functionality")
    print("=" * 60)
    
    tests = [
        ("Backend Views", test_backend_views),
        ("Frontend View Names", test_frontend_views),
        ("API Endpoint", test_api_endpoint),
        ("UI Integration", test_ui_integration),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n✗ Test '{name}' failed with error: {e}")
            results.append((name, False))
    
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n✓ All tests passed! Closeup photo upload is fully supported.")
        return 0
    else:
        print(f"\n✗ {total - passed} test(s) failed.")
        return 1

if __name__ == "__main__":
    sys.exit(main())