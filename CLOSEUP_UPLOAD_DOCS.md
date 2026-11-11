# Closeup Photo Upload Documentation

## Overview

The Video Game Asset Creator supports uploading closeup photos for turnaround views. This allows users to provide their own reference images for different views of an asset (front, left, back, right, three-quarter, and top).

## Supported Views

The system supports the following predefined views:

- `front`: Front view of the asset
- `left`: Left side view
- `back`: Back view
- `right`: Right side view
- `quarter`: Three-quarter view
- `top`: Top-down view

These views are defined in the backend reference data:
```python
TURNAROUND_VIEWS = ("front", "left", "back", "right", "quarter", "top")
```

## Frontend Integration

### View Names

The frontend provides user-friendly names for each view in `OwnPictures.tsx`:

```typescript
export const VIEW_NAMES: Record<string, string> = { 
  front: 'Front', 
  left: 'Left side', 
  back: 'Back', 
  right: 'Right side', 
  quarter: 'Three-quarter', 
  top: 'Top' 
}
```

### UI Components

1. **OwnPictures Component**: The main container that renders the upload interface based on the current stage.

2. **OwnViews Component**: Specifically handles the turnaround stage and displays slots for each view.

3. **ImageDrop Component**: Allows users to drop or select images for each view.

4. **FacingDiagram Component**: Visual indicator showing the camera position for each view.

## API Endpoint

### Upload View Endpoint

```typescript
uploadView: (vid: string, view: string, image: string) => 
  post<RunResult>(`/api/versions/${vid}/views/${view}/upload`, { image })
```

**Parameters:**
- `vid` (string): The version ID
- `view` (string): The view name (must be one of the supported views)
- `image` (string): The image data URL (base64 encoded)

**Returns:**
- `RunResult`: Contains the updated version information

### Usage Example

```typescript
const r = await api.uploadView(onVersion.id, view, p.dataUrl)
setPictures((x) => ({ ...x, [view]: null }))
onCreated(r.version.id)
```

## Workflow

1. **User Action**: User clicks "I have my own side or back views" button
2. **Panel Opens**: A panel with slots for each view appears
3. **Image Selection**: User can drop or select images for any view
4. **Upload**: When an image is added to a view slot, a "Use for the [view] view" button appears
5. **Version Creation**: Clicking the button uploads the image and creates a new version
6. **Multiple Views**: User can add images to multiple views
7. **Draw Other Sides**: User can click "Draw the other sides, using mine" to generate the remaining views based on the uploaded images

## Backend Support

The backend supports:
- Uploading images for individual views
- Validating that the view name is one of the supported views
- Creating new versions with the uploaded images
- Comparing uploaded views to ensure consistency

## Testing

A comprehensive test script (`test_closeup_upload.py`) validates:

1. **Backend Views**: Verifies that the backend supports all expected views
2. **Frontend View Names**: Ensures the frontend VIEW_NAMES matches backend views
3. **API Endpoint**: Confirms the uploadView endpoint is properly defined
4. **UI Integration**: Checks that all views are properly rendered in the UI

Run the tests with:
```bash
python test_closeup_upload.py
```

## Limitations and Notes

- Images must be in a format that can be encoded as data URLs (typically PNG or JPEG)
- The system validates that uploaded views are consistent with the front view
- Views that don't match may be kept with a warning
- The three-quarter view is a special case that shows the asset at an angle
- The top view shows the asset from directly above

## Future Enhancements

Potential improvements could include:
- Support for additional views (e.g., bottom, isometric)
- Better validation and error messages for inconsistent views
- Preview of how the uploaded images will be used in the turnaround
- Batch upload functionality for multiple views at once