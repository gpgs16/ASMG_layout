import cv2
import numpy as np
import easyocr
from google.adk.tools import BaseTool
from typing import Dict, Any


class ComponentDetector(BaseTool):
    """
    Computer vision tool that detects components in layout diagrams using contour detection.
    It draws the detected components on the image and returns their bounding boxes.
    """

    def __init__(self):
        super().__init__(
            name="ComponentDetector",
            description="Detects components in a layout diagram, extracts their type via OCR, and returns bounding boxes and component types.",
        )
        # Initialize the OCR reader once to avoid reloading the model on every call
        self.ocr_reader = easyocr.Reader(['en'], gpu=False)

    async def run_async(self, image_data: bytes) -> Dict[str, Any]:
        """
        Detects components in the image, saves an annotated image, and returns bounding boxes.

        Args:
            image_data: Raw image bytes

        Returns: A dictionary containing bounding boxes, component types, and the annotated image.
        """
        print("\n--- EXECUTING TOOL: ComponentDetector ---")
        try:
            # Convert bytes to OpenCV image
            nparr = np.frombuffer(image_data, np.uint8)
            original_image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if original_image is None:
                print("Error: Could not decode image data.")
                return {"error": "Could not decode image data."}

            gray = cv2.cvtColor(original_image, cv2.COLOR_BGR2GRAY)

            # 1. Detect contours
            blurred = cv2.GaussianBlur(gray, (3, 3), 0)
            binary = cv2.adaptiveThreshold(
                blurred, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 15, 5
            )
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
            binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)
            binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=2)
            cnts, _ = cv2.findContours(
                binary, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE
            )

            # 2. Filter contours
            valid_contours = []
            # Define a list of valid (width_range, height_range) pairs for different component sizes.
            # The ranges are defined as (min, max).
            valid_dimension_ranges = [
                        ((70, 90),   (70, 90)),  
                        ((100, 570), (70, 90)),   
                        ((70, 90),   (230, 250)),   
            ]

            for c in cnts:
                x, y, w, h = cv2.boundingRect(c)
                area = cv2.contourArea(c)

                # Check if the contour's dimensions fall into any of the valid ranges
                for w_range, h_range in valid_dimension_ranges:
                    if (w_range[0] <= w <= w_range[1]) and (h_range[0] <= h <= h_range[1]):
                        valid_contours.append((x, y, w, h, area))
                        break  # Found a match, no need to check other ranges

            # 3. Remove overlaps
            valid_contours.sort(key=lambda item: item[4], reverse=True)  # Sort by area
            final_contours = []
            for x1, y1, w1, h1, a1 in valid_contours:
                overlap = False
                for x2, y2, w2, h2, _ in final_contours:
                    overlap_x = max(0, min(x1 + w1, x2 + w2) - max(x1, x2))
                    overlap_y = max(0, min(y1 + h1, y2 + h2) - max(y1, y2))
                    overlap_area = overlap_x * overlap_y
                    smaller_area = min(w1 * h1, w2 * h2)
                    if overlap_area > 0.3 * smaller_area:
                        overlap = True
                        break
                if not overlap:
                    final_contours.append((x1, y1, w1, h1, a1))

            # 4. Draw results on image and save it
            result_img = original_image.copy()
            for i, (x, y, w, h, _) in enumerate(final_contours):
                #cv2.rectangle(result_img, (x, y), (x + w, y + h), (0, 0, 255), 2)
                text_x = x + 1
                text_y = y + 21
                cv2.putText(
                    result_img,
                    str(i),
                    (text_x, text_y),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (0, 0, 255),
                    2,
                )

            # 5. Prepare and return the bounding box dictionary
            bounding_boxes = {}
            for i, (x, y, w, h, _) in enumerate(final_contours):
                bounding_boxes[i] = {"x": x, "y": y, "length": w, "width": h}

            # 6. Extract text from each contour using OCR
            component_types = {}
            for i, (x, y, w, h, _) in enumerate(final_contours):
                # Add padding to capture the full character
                padding = -12
                x_start, y_start = max(0, x - padding), max(0, y - padding)
                x_end, y_end = min(original_image.shape[1], x + w + padding), min(original_image.shape[0], y + h + padding)

                # Extract the region of interest
                roi = original_image[y_start:y_end, x_start:x_end]

                # --- Replicating preprocessing from contour_threshold.py ---
                # Convert to grayscale
                roi_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                # Resize ROI to improve OCR
                scale_factor = 3
                roi_resized = cv2.resize(roi_gray, None, fx=scale_factor, fy=scale_factor, interpolation=cv2.INTER_CUBIC)
                # Apply binary threshold
                _, roi_binary = cv2.threshold(roi_resized, 127, 255, cv2.THRESH_BINARY)
                
                ocr_results = self.ocr_reader.readtext(roi_binary, allowlist='CDLMU0123456789', detail=1)
                
                best_text = 'UNKNOWN'
                if ocr_results:
                    # Get the result with the highest confidence
                    best_result = max(ocr_results, key=lambda item: item[2])
                    detected_text = best_result[1].upper().strip()
                    if detected_text:
                        best_text = detected_text
                
                component_types[str(i)] = best_text
            
            print(f"--- ComponentDetector OCR Result: {component_types} ---")

            # Encode the result image to bytes to return it
            is_success, buffer = cv2.imencode(".png", result_img)
            if not is_success:
                return {"error": "Failed to encode result image."}
            numbered_image_bytes = buffer.tobytes()

            print(f"--- ComponentDetector Result: Found {len(bounding_boxes)} components ---")
            # Return bounding boxes, the new component types, and the annotated image
            return {
                "box_data": bounding_boxes,
                "component_types": component_types,
                "numbered_image_bytes": numbered_image_bytes
            }

        except Exception as e:
            print(f"Error in ComponentDetector: {e}")
            return {"error": str(e)}