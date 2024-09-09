import os
from typing import List

from src.algorithm.template.detection.base.detection_pass import TemplateDetectionPass
from src.algorithm.template.detection.base.file_runner import (
    FieldTypeFileLevelRunner,
    FileLevelRunner,
)
from src.algorithm.template.detection.base.interpretation_pass import (
    TemplateInterpretationPass,
)
from src.algorithm.template.detection.bubbles_threshold.file_runner import (
    BubblesThresholdFileRunner,
)
from src.algorithm.template.detection.ocr.file_runner import OCRFileRunner
from src.algorithm.template.template_layout import Field
from src.processors.constants import FieldDetectionType

"""
Template Detector takes care of detections of an image file using a single template
We create one instance of TemplateFileLevelRunner per Template.
Note: a Template may get reused for multiple directories(in nested case)
"""


"""
TemplateFileLevelRunner maintains own template level runners as well as all the field detection type level runners.
"""


class TemplateFileLevelRunner(FileLevelRunner):
    field_detection_type_to_runner = {
        FieldDetectionType.BUBBLES_THRESHOLD: BubblesThresholdFileRunner,
        FieldDetectionType.OCR: OCRFileRunner,
        # FieldDetectionType.BARCODE_QR: BarcodeQRRunner,
        # FieldDetectionType.BUBBLES_BLOB: BubblesBlobRunner,
    }

    def __init__(self, template):
        tuning_config = template.tuning_config
        detection_pass = TemplateDetectionPass(tuning_config)
        interpretation_pass = TemplateInterpretationPass(tuning_config)
        super().__init__(tuning_config, detection_pass, interpretation_pass)
        self.template = template
        self.all_fields: List[Field] = template.all_fields
        self.all_field_detection_types = self.template.all_field_detection_types

        self.prepare_field_detection_type_runners()

        initial_directory_path = os.path.dirname(template.path)
        self.initialize_directory_level_aggregates(initial_directory_path)

        # Cast to correct type (TODO: this should not be needed -)
        self.detection_pass: TemplateDetectionPass = self.detection_pass
        self.interpretation_pass: TemplateInterpretationPass = self.interpretation_pass

    def prepare_field_detection_type_runners(self):
        # Create instances of all required field type processors
        self.field_detection_type_runners = {
            field_detection_type: self.get_field_detection_type_runner(
                field_detection_type
            )
            for field_detection_type in self.all_field_detection_types
        }

    def get_field_detection_type_runner(
        self, field_detection_type
    ) -> FieldTypeFileLevelRunner:
        tuning_config = self.tuning_config
        FieldTypeProcessorClass = self.field_detection_type_to_runner[
            field_detection_type
        ]
        return FieldTypeProcessorClass(tuning_config)

    def read_omr_and_update_metrics(self, file_path, gray_image, colored_image):
        # First pass to compute aggregates like global threshold
        # TODO: populate local thresholds even in first pass? (to enable multiple passes)

        # populate detections
        self.run_file_level_detection(file_path, gray_image, colored_image)

        # populate interpretations
        omr_response = self.run_file_level_interpretation(
            file_path, gray_image, colored_image
        )

        return omr_response

    # FieldTypeFileLevelRunner::run_field_level_detection
    def run_file_level_detection(self, file_path, gray_image, colored_image):
        self.initialize_file_level_detection_aggregates(file_path)

        # Perform detection step for each field
        # TODO: see where the conditional sets logic can fit in this loop (or at a wrapper level?)
        for field in self.all_fields:
            self.run_field_level_detection(field, gray_image, colored_image)

        self.update_detection_aggregates_on_processed_file(file_path)

    def run_field_level_detection(self, field: Field, gray_image, colored_image):
        self.detection_pass.initialize_field_level_aggregates(field)

        field_detection_type_runner = self.field_detection_type_runners[
            field.field_detection_type
        ]

        field_detection = field_detection_type_runner.run_field_level_detection(
            field, gray_image, colored_image
        )

        self.detection_pass.update_aggregates_on_processed_field_detection(
            field, field_detection
        )

    # Overrides
    def initialize_directory_level_aggregates(self, initial_directory_path):
        # super().initialize_directory_level_aggregates(initial_directory_path)

        self.detection_pass.initialize_directory_level_aggregates(
            initial_directory_path, self.all_field_detection_types
        )
        self.interpretation_pass.initialize_directory_level_aggregates(
            initial_directory_path, self.all_field_detection_types
        )

        for field_detection_type_runner in self.field_detection_type_runners.values():
            field_detection_type_runner.initialize_directory_level_aggregates(
                initial_directory_path
            )

    def initialize_file_level_detection_aggregates(self, file_path):
        # super().initialize_file_level_detection_aggregates(file_path)
        self.detection_pass.initialize_file_level_aggregates(
            file_path, self.all_field_detection_types
        )

        # Setup field type wise metrics
        for field_detection_type_runner in self.field_detection_type_runners.values():
            field_detection_type_runner.initialize_file_level_detection_aggregates(
                file_path
            )

    def update_detection_aggregates_on_processed_file(self, file_path):
        for field_detection_type_runner in self.field_detection_type_runners.values():
            field_detection_type_runner.update_detection_aggregates_on_processed_file(
                file_path
            )

        self.detection_pass.update_aggregates_on_processed_file(
            file_path, self.field_detection_type_runners
        )

    # TODO: move into template_interpreter as a subclass of TemplatePass?
    def run_file_level_interpretation(self, file_path, gray_image, colored_image):
        self.initialize_file_level_interpretation_aggregates(file_path)

        current_omr_response = {}
        # Perform interpretation step for each field
        for field in self.all_fields:
            self.run_field_level_interpretation(current_omr_response, field)

        self.update_interpretation_aggregates_on_processed_file(
            file_path, current_omr_response
        )

        return current_omr_response

    def run_field_level_interpretation(self, current_omr_response, field):
        field_label = field.field_label

        field_detection_type_runner = self.field_detection_type_runners[
            field.field_detection_type
        ]
        field_interpretation = (
            field_detection_type_runner.run_field_level_interpretation(field)
        )

        field_level_interpretation_aggregates = (
            field_detection_type_runner.get_field_level_interpretation_aggregates()
        )

        self.interpretation_pass.update_aggregates_on_processed_field_interpretation(
            current_omr_response,
            field,
            field_interpretation,
            field_level_interpretation_aggregates,
        )

        detected_string = field_interpretation.get_detected_string()
        current_omr_response[field_label] = detected_string

    def initialize_file_level_interpretation_aggregates(self, file_path):
        # Note: Interpretation loop needs access to the file level detection aggregates
        all_file_level_detection_aggregates = (
            self.detection_pass.directory_level_aggregates["file_level_aggregates"][
                file_path
            ]
        )
        field_detection_type_wise_detection_aggregates = (
            all_file_level_detection_aggregates["field_detection_type_wise_aggregates"]
        )
        field_label_wise_aggregates = all_file_level_detection_aggregates[
            "field_label_wise_aggregates"
        ]

        self.interpretation_pass.initialize_file_level_interpretation_aggregates(
            file_path,
            self.all_field_detection_types,
            field_detection_type_wise_detection_aggregates,
            field_label_wise_aggregates,
        )

        # Setup field type wise metrics
        for field_detection_type_runner in self.field_detection_type_runners.values():
            field_detection_type_runner.initialize_file_level_interpretation_aggregates(
                file_path,
                field_detection_type_wise_detection_aggregates,
                field_label_wise_aggregates,
            )

    def get_file_level_interpretation_aggregates(self):
        return self.interpretation_pass.file_level_aggregates

    def update_interpretation_aggregates_on_processed_file(
        self, file_path, current_omr_response
    ):
        for field_detection_type_runner in self.field_detection_type_runners.values():
            field_detection_type_runner.update_interpretation_aggregates_on_processed_file(
                file_path
            )

        # Note: we update file level after field levels are updated
        self.interpretation_pass.update_aggregates_on_processed_file(
            file_path, self.field_detection_type_runners
        )

    def finalize_directory_metrics(self):
        # TODO: get_directory_level_confidence_metrics()

        # output_metrics = self.directory_level_aggregates
        # TODO: export directory level stats here
        pass
