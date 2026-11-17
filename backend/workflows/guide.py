"""Step-by-step guide for the platform workflow builder."""
import io


def build_workflow_guide_pdf():
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        'guide-title', parent=styles['Title'], fontName='Times-Bold', fontSize=18, leading=22, spaceAfter=8,
    )
    h = ParagraphStyle(
        'guide-h', parent=styles['Heading2'], fontName='Times-Bold', fontSize=13, leading=16,
        spaceBefore=12, spaceAfter=4, textColor='#102033',
    )
    body = ParagraphStyle(
        'guide-body', parent=styles['BodyText'], fontName='Times-Roman', fontSize=10.5, leading=14,
        alignment=TA_LEFT, spaceAfter=6,
    )
    step = ParagraphStyle(
        'guide-step', parent=body, leftIndent=12, bulletIndent=0, spaceAfter=3,
    )

    story = [
        Paragraph('Working with workflows', title),
        Paragraph(
            'Health Focus Diagnostics — laboratory and radiology catalogue. '
            'This guide matches the screen at Platform → Workflows.',
            body,
        ),
        Paragraph('1. What a workflow is', h),
        Paragraph(
            'A workflow is the approved path an order must follow, from registration through release. '
            'Each published version is locked. Orders already in progress stay on the version they were bound to. '
            'A new version applies only to orders created after you publish it.',
            body,
        ),
        Paragraph(
            'Four starting templates are available: Normal laboratory, Microbiology, Cytology, and Histology. '
            'A centre can clone any of them or create a new code, including a radiology path, and map it to tests or investigations.',
            body,
        ),
        Paragraph('2. Open the builder', h),
        _steps(step, [
            'Sign in as a platform administrator.',
            'Open Platform controls, then Workflows. The address is /platform/workflows/.',
            'The page has three parts: a step palette on the left, the canvas in the centre, and the detail panel below once a workflow is open.',
            'Use the pills at the top (All workflows, each workflow name, and + New) to filter the canvas.',
        ]),
        Paragraph('3. Create a draft', h),
        _steps(step, [
            'Choose + New.',
            'Enter a short code (for example NORMAL or RADIOLOGY). Spaces become underscores and the code is stored in capitals.',
            'Enter the display name staff will recognise, and an overall turnaround in minutes if you know it.',
            'Add a short description, then choose Create draft.',
            'The new row is a draft. Drafts can be edited. Published versions cannot.',
        ]),
        Paragraph('4. Add the steps', h),
        Paragraph(
            'A step is one station in the path, such as payment, collection, result entry, review, authentication, or release. '
            'Order matters: the first step is where a new order starts.',
            body,
        ),
        _steps(step, [
            'Open the draft from the canvas.',
            'Drag a chip from the Steps palette onto the canvas, or use Add step in the detail panel.',
            'Give the step a code (for example REVIEW) and a name staff will see.',
            'Set the department, the sample status, and the result status this step represents.',
            'Choose the roles allowed to leave the step. Leave roles empty only when any laboratory role may do the work.',
            'Tick the fields that must be present (for example narrative_or_values) and any visit document that must already be on file.',
            'Enter the step turnaround and the minutes after which it should escalate, and the roles to notify.',
            'Write the instruction the bench will follow. Mark the step mandatory, or allow skip only when leadership has agreed a bypass.',
            'Save the step. To change the sequence, drag the nodes on an open draft and choose Save workflow.',
        ]),
        Paragraph('5. Connect the steps', h),
        Paragraph(
            'Nothing moves unless a transition allows it. The server rejects an action that is not drawn from the current step.',
            body,
        ),
        _steps(step, [
            'In Transitions, choose the From step, the To step, and the action code (for example collect, submit, authenticate, release).',
            'Add a plain label such as Authenticate.',
            'Tick Require paid when the visit must be paid or on approved credit before this move.',
            'Choose a required document if one must be attached first.',
            'Leave Enforce required fields ticked so the step’s required fields are checked.',
            'Add the transition. Repeat until every legal move is listed, including any agreed skip.',
            'Remove a transition only while the version is still a draft.',
        ]),
        Paragraph('6. Preview, then publish', h),
        _steps(step, [
            'Choose Preview. Read the ordered list of steps before you activate anything.',
            'Confirm there is at least one step and at least one transition. Publish is refused without them.',
            'Choose Publish / activate. Confirm the prompt.',
            'That version becomes the active one for new orders. Any older published version of the same code is no longer used for new orders.',
            'In-progress orders keep the version they were bound to. Do not expect a publish to rewrite them.',
        ]),
        Paragraph('7. Change a live workflow safely', h),
        _steps(step, [
            'Open the published workflow.',
            'Choose New version (clone). A draft copy is created. The live version stays in force.',
            'Edit steps and transitions on the draft only.',
            'Preview, then publish the new version when laboratory leadership has agreed it.',
            'New orders bind to the new version. Orders already on the previous version finish on that version.',
        ]),
        Paragraph('8. Map a workflow to each laboratory test', h),
        _steps(step, [
            'Publish the workflow first. Unpublished drafts do not appear on the test form.',
            'Open Catalog → Laboratory tests.',
            'On Create test, or on an existing test, choose Workflow. The list shows the name and code of each published workflow.',
            'Save the test. The platform stores the code on the test and creates a test-level assignment (priority 10).',
            'A test-level assignment wins over a service-group assignment. New laboratory orders bind this workflow at creation and follow only its transitions.',
            'You can also assign from the workflow page: open the workflow, choose the laboratory test, and Assign. Pick only one of test, investigation, or service group.',
        ]),
        Paragraph('9. Map a workflow to each radiology investigation', h),
        _steps(step, [
            'Create or publish the workflow the imaging service should use. A radiology path can be its own code.',
            'Open Catalog → Imaging.',
            'On Create procedure, or when editing a procedure, choose Workflow.',
            'Save. The investigation now carries that workflow code, and an assignment is stored against the procedure.',
            'From the workflow page you can assign the same published workflow directly to a radiology investigation.',
            'Service-group assignments remain a fallback for laboratory tests that have no test-level mapping. Investigations use their own mapping.',
        ]),
        Paragraph('10. How an order uses the workflow', h),
        Paragraph(
            'When a laboratory order is created, the platform binds the active published version of the mapped workflow. '
            'The order starts on the first step. Each later action — collect, receive, enter result, submit, review, authenticate, release — '
            'must match a transition from the current step. The user’s role must be allowed on that step. '
            'Payment and document gates are enforced when the transition says so. '
            'The history of moves is kept on the order and is not rewritten if you later publish a new version.',
            body,
        ),
        Paragraph('11. Seeded laboratory paths', h),
        Paragraph(
            'Normal laboratory: order, payment, collection, batch, department receive, processing, result entry, review, authentication, release.',
            body,
        ),
        Paragraph(
            'Microbiology adds culture and incubation between receiving and result entry. '
            'Cytology uses a cytology pending and processing path, with an optional skip of department receiving when that transition is configured. '
            'Histology uses accessioning, grossing, embedding, and microscopy before the report is entered, reviewed, authenticated, and released.',
            body,
        ),
        Paragraph('12. Result templates, letterhead, and the authenticator’s signature', h),
        Paragraph(
            'Workflows control who may move an order. The printed result is controlled separately, and both are required for a complete report.',
            body,
        ),
        _steps(step, [
            'Build the result layout under Platform → Result templates. Add the catalogue parameters, set the order, and publish the version. Open Report preview on the builder and switch the centre to see that centre’s letterhead.',
            'A centre administrator uploads the header and footer under Catalog → Centres → Result formatting. PNG, JPG, and a one-page PDF are accepted for each. Text header and footer lines remain available when no artwork is uploaded.',
            'The person who performs final authentication uploads a signature image under Account → Signature, with title, qualifications, and registration number on the profile.',
            'At authentication the platform freezes the template version, the centre letterhead, and that user’s signature onto the result. A later change to the template, the letterhead, or the profile does not alter a result that has already been authenticated.',
            'Release stays a separate step, performed by a different authorised user, and still has to be allowed by the workflow.',
        ]),
        Spacer(1, 8),
        Paragraph(
            'Keep codes stable, publish only after the preview reads correctly, and map every new test and investigation before it is ordered.',
            body,
        ),
    ]

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title='Working with workflows', author='Health Focus Diagnostics',
    )
    doc.build(story)
    return buffer.getvalue()


def _steps(style, lines):
    from reportlab.platypus import ListFlowable, ListItem, Paragraph
    return ListFlowable(
        [ListItem(Paragraph(line, style), leftIndent=14) for line in lines],
        bulletType='1', start='1', leftIndent=16, spaceBefore=2, spaceAfter=6,
    )
