"""DICOM connectivity and file inspection. Pixel rendering stays with the PACS viewer."""


def dicom_metadata(file_obj):
    import pydicom

    file_obj.seek(0)
    dataset = pydicom.dcmread(file_obj, stop_before_pixels=True, force=True)
    file_obj.seek(0)
    return {
        'patient_id': str(getattr(dataset, 'PatientID', '') or ''),
        'study_date': str(getattr(dataset, 'StudyDate', '') or ''),
        'modality': str(getattr(dataset, 'Modality', '') or ''),
        'study_description': str(getattr(dataset, 'StudyDescription', '') or ''),
        'sop_class_uid': str(getattr(dataset, 'SOPClassUID', '') or ''),
        'accession_number': str(getattr(dataset, 'AccessionNumber', '') or ''),
    }


def echo_modality(modality, calling_ae='HFDN_SCU'):
    if not modality.host or not modality.port:
        raise ValueError('This modality has no host and port to echo.')
    from pynetdicom import AE
    from pynetdicom.sop_class import Verification

    ae = AE(ae_title=calling_ae[:16])
    ae.add_requested_context(Verification)
    association = ae.associate(
        str(modality.host), int(modality.port),
        ae_title=(modality.ae_title or 'ANY-SCP')[:16],
        timeout=5,
    )
    try:
        if not association.is_established:
            return False, 'DICOM association was not accepted.'
        status = association.send_c_echo()
        if status and int(status.Status) == 0x0000:
            return True, 'DICOM C-ECHO succeeded.'
        code = getattr(status, 'Status', None)
        return False, f'DICOM C-ECHO returned status {code}.'
    finally:
        if association.is_established:
            association.release()
