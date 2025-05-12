import frappe
from urllib.parse import urljoin

def execute():
    # Fetch S3 configuration
    s3_config = frappe.get_doc("S3 File Attachment", "S3 File Attachment")
    bucket_name = s3_config.bucket_name
    region_name = s3_config.region_name

    # Get a list of single doctypes
    single_doctypes = [doc['name'] for doc in frappe.get_list(
        'DocType',
        filters={'issingle': 1},
        fields=['name']
    )]

    # Fetch the file records
    records = frappe.db.sql("""
    SELECT name, file_url, content_hash, attached_to_doctype FROM `tabFile` 
    """, as_dict=True)

    if records:
        for record in records:
            s3_key = record.content_hash
            url = record.file_url
            updated_url = None

            # Handle Single Doctypes: Update the URL with replacements and base URL
            if record.attached_to_doctype in single_doctypes:
                # URL encode '#' as '%23' for single-doctype records
                updated_url = url.replace('#', '%23')
                
                # Create full URL by joining the base URL with the file_url
                base_url = frappe.utils.get_url()
                updated_url = urljoin(base_url, updated_url)

            else:
                # Handle Non-Single Doctypes: Construct the S3 URL
                if url and url.startswith("http"):
                    updated_url = url  
                else:
                    updated_url = f"https://{bucket_name}.s3.{region_name}.amazonaws.com/{s3_key}"

            # Update the record with the new custom_updated_url
            frappe.db.sql("""
                UPDATE `tabFile`
                SET custom_updated_url=%s
                WHERE name=%s
            """, (updated_url, record.name))

        frappe.db.commit()
