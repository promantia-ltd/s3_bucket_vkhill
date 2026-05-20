from __future__ import unicode_literals

import datetime
import os
import random
import re
import string
import shutil

import boto3

from botocore.client import Config
from botocore.exceptions import ClientError

import frappe
from urllib.parse import urljoin
import magic
from frappe.utils import cint
URL_PREFIXES = ("http://", "https://")


def is_s3_upload_disabled():
    try:
        return cint(frappe.db.get_single_value("S3 File Attachment", "disable_s3_upload"))
    except Exception:
        return 0


def validate_s3_settings(settings):
    missing_fields = []

    if not settings.bucket_name:
        missing_fields.append("Bucket Name")
    if not settings.region_name:
        missing_fields.append("S3 Bucket Region Name")
    if not settings.folder_name:
        missing_fields.append("Folder Name")
    if not settings.aws_key:
        missing_fields.append("AWS Key")
    if not settings.aws_secret:
        missing_fields.append("AWS Secret")

    if missing_fields:
        frappe.throw(
            frappe._(
                "S3 upload is not configured. Either check 'Disable S3 Upload' in S3 File Attachment "
                "or configure: {0}."
            ).format(", ".join(missing_fields))
        )


class S3Operations(object):

    def __init__(self):
        """
        Function to initialise the aws settings from frappe S3 File attachment
        doctype.
        """
        self.s3_settings_doc = frappe.get_doc(
            'S3 File Attachment',
            'S3 File Attachment',
        )
        validate_s3_settings(self.s3_settings_doc)
        if (
            self.s3_settings_doc.aws_key and
            self.s3_settings_doc.aws_secret
        ):
            self.S3_CLIENT = boto3.client(
                's3',
                aws_access_key_id=self.s3_settings_doc.aws_key,
                aws_secret_access_key=self.s3_settings_doc.aws_secret,
                region_name=self.s3_settings_doc.region_name,
                config=Config(signature_version='s3v4')
            )
        else:
            self.S3_CLIENT = boto3.client(
                's3',
                region_name=self.s3_settings_doc.region_name,
                config=Config(signature_version='s3v4')
            )
        self.BUCKET = self.s3_settings_doc.bucket_name
        self.folder_name = self.s3_settings_doc.folder_name

    def strip_special_chars(self, file_name):
        """
        Strips file charachters which doesnt match the regex.
        """
        regex = re.compile('[^0-9a-zA-Z._-]')
        file_name = regex.sub('', file_name)
        return file_name

    def key_generator(self, file_name, parent_doctype, parent_name, file_path):
        """
        Generate keys for s3 objects uploaded with file name attached.
        """
        hook_cmd = frappe.get_hooks().get("s3_key_generator")
        if hook_cmd:
            try:
                k = frappe.get_attr(hook_cmd[0])(
                    file_name=file_name,
                    parent_doctype=parent_doctype,
                    parent_name=parent_name
                )
                if k:
                    return k.rstrip('/').lstrip('/')
            except:
                pass

        file_name = file_name.replace(' ', '_')
        file_name = self.strip_special_chars(file_name)
        key = ''.join(
            random.choice(
                string.ascii_uppercase + string.digits) for _ in range(8)
        )


        doc_path = None

        if not doc_path:
            final_key = "/".join(file_path.split("/")[1:-1]) + "/" + file_name
            return final_key
        else:
            final_key = doc_path + '/' + key + "_" + file_name
            return final_key

    def upload_files_to_s3_with_key(
            self, file_path, file_name, is_private, parent_doctype, parent_name
    ):
        """
        Uploads a new file to S3.
        Strips the file extension to set the content_type in metadata.
        """
        if file_path:
            try:
                mime_type = magic.from_file(file_path, mime=True)
                key = self.key_generator(file_name, parent_doctype, parent_name, file_path)
                content_type = mime_type
                
            except FileNotFoundError:
                frappe.log_error(f"File not found: {file_name}",  )
                return None 
            try:
                if is_private:
                    self.S3_CLIENT.upload_file(
                        file_path, self.BUCKET, key,
                        ExtraArgs={
                            "ContentType": content_type,
                            "Metadata": {
                                "ContentType": content_type,
                                "file_name": file_name
                            }
                        }
                    )
                else:
                    self.S3_CLIENT.upload_file(
                        file_path, self.BUCKET, key,
                        ExtraArgs={
                            "ContentType": content_type,
                            # "ACL": 'public-read',
                            "Metadata": {
                                "ContentType": content_type,

                            }
                        }
                    )

            except boto3.exceptions.S3UploadFailedError:
                frappe.throw(frappe._("File Upload Failed. Please try again."))
            return key

    def delete_from_s3(self, key):
        """Delete file from s3"""
        self.s3_settings_doc = frappe.get_doc(
            'S3 File Attachment',
            'S3 File Attachment',
        )

        if self.s3_settings_doc.delete_file_from_cloud:
            s3_client = boto3.client(
                's3',
                aws_access_key_id=self.s3_settings_doc.aws_key,
                aws_secret_access_key=self.s3_settings_doc.aws_secret,
                region_name=self.s3_settings_doc.region_name,
                config=Config(signature_version='s3v4')
            )

            try:
                s3_client.delete_object(
                    Bucket=self.s3_settings_doc.bucket_name,
                    Key=key
                )
            except ClientError:
                frappe.throw(frappe._("Access denied: Could not delete file"))

    def read_file_from_s3(self, key):
        """
        Function to read file from a s3 file.
        """
        return self.S3_CLIENT.get_object(Bucket=self.BUCKET, Key=key)

    def get_url(self, key, file_name=None):
        """
        Return url.

        :param bucket: s3 bucket name
        :param key: s3 object key
        """
        if self.s3_settings_doc.signed_url_expiry_time:
            self.signed_url_expiry_time = self.s3_settings_doc.signed_url_expiry_time # noqa
        else:
            self.signed_url_expiry_time = 120
        params = {
                'Bucket': self.BUCKET,
                'Key': key,

        }
        if file_name:
            params['ResponseContentDisposition'] = 'filename={}'.format(file_name)

        url = self.S3_CLIENT.generate_presigned_url(
            'get_object',
            Params=params,
            ExpiresIn=self.signed_url_expiry_time,
        )

        return url


@frappe.whitelist()
def file_upload_to_s3(doc, method):
    """
    check and upload files to s3. the path check and
    """
    if is_s3_upload_disabled():
        return

    if doc.is_folder:
        return
    if doc.attached_to_doctype  in [ "Prepared Report",   "Repost Item Valuation", "Chart of Accounts Importer", "Bank Statement Import" ]:
        return
    
    s3_upload = S3Operations()
    path = doc.file_url
    site_path = frappe.utils.get_site_path()
    parent_doctype = doc.attached_to_doctype or 'File'
    parent_name = doc.attached_to_name
    ignore_s3_upload_for_doctype = frappe.local.conf.get('ignore_s3_upload_for_doctype') or ['Data Import'] 
    if parent_doctype not in ignore_s3_upload_for_doctype:
        if not doc.is_private:
            if  path.startswith("http"):
                file_path =path
            else:
                file_path = site_path + '/public' + path
        else:
            file_path = site_path + path
        # else:
        #     file_path=doc.file_url
    
        if not  path.startswith("http"):
            key = s3_upload.upload_files_to_s3_with_key(
                file_path, doc.file_name,
                doc.is_private, parent_doctype,
                parent_name
            )

            if doc.is_private:
                method = "frappe_s3_attachment.controller.generate_file"
                file_url = """/api/method/{0}?key={1}&file_name={2}""".format(method, key, doc.file_name)
            else:
                method = "frappe_s3_attachment.controller.generate_file"
                file_url = """/api/method/{0}?key={1}&file_name={2}""".format(method, key, doc.file_name)
                
            os.remove(file_path)
            
            bucket_name = s3_upload.BUCKET
            region_name = s3_upload.S3_CLIENT.meta.region_name
            updated_url = file_url
            
            if not file_url.startswith("http"):
                updated_url = f"https://{bucket_name}.s3.{region_name}.amazonaws.com/{key}"
        
            
            frappe.db.sql("""
                            UPDATE `tabFile` SET 
                                file_url=%s,
                                custom_updated_url=%s,
                                old_parent=%s, 
                                content_hash=%s 
                            WHERE name=%s
                        """, (
                            file_url,              
                            updated_url,           
                            'Home/Attachments',    
                            key,                   
                            doc.name               
                        ))
            
            doc.file_url = file_url
            
            if parent_doctype and frappe.get_meta(parent_doctype).get('image_field'):
                frappe.db.set_value(parent_doctype, parent_name, frappe.get_meta(parent_doctype).get('image_field'), file_url)

            frappe.db.commit()
    

@frappe.whitelist()
def generate_file(key=None, file_name=None):
    """
    Function to stream file from s3.
    """
    if key:
        s3_upload = S3Operations()
        signed_url = s3_upload.get_url(key, file_name)
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = signed_url
    else:
        frappe.local.response['body'] = "Key not found."

def move_file(source_path, destination_path):
    """
    Move a file from the source path to the destination path.

    This function creates the necessary directories in the destination path if they do not exist,
    and then moves the file from the source path to the destination path using the shutil.move function.

    Parameters:
    source_path (str): The current location of the file.
    destination_path (str): The desired location of the file.

    Returns:
    None
    """
    os.makedirs(os.path.dirname(destination_path), exist_ok=True)
    shutil.move(source_path, destination_path)

def upload_existing_files_s3(name, file_name):
    if is_s3_upload_disabled():
        return

    # Get single doctypes and extract names into a list
    single_doctypes = [doc['name'] for doc in frappe.get_list(
    'DocType',
    filters={'issingle': 1},
    fields=['name']
)]

    """
    Function to upload all existing files.
    """
    file_doc_name = frappe.db.get_value('File', {'name': name})
    if file_doc_name:
        doc = frappe.get_doc('File', name)
        if doc.attached_to_doctype in single_doctypes :
            return
        
        s3_upload = S3Operations()
        path = doc.file_url
        site_path = frappe.utils.get_site_path()
        parent_doctype = doc.attached_to_doctype
        parent_name = doc.attached_to_name
        

        source_path = file_path = site_path + '/public' + path
        if doc.is_private:
            source_path = file_path = site_path + path

        if "configurable_attachment_folder" in frappe.get_installed_apps():
            from configurable_attachment_folder.overrides.file import path_finder

            if folder_path := path_finder(parent_doctype, parent_name):
                file_path = site_path + "/public/files/" + folder_path + doc.file_name
                if doc.is_private:
                    file_path = site_path + "/private/files/" + folder_path + doc.file_name

                if source_path != file_path:
                    move_file(source_path, file_path)
            
        key = s3_upload.upload_files_to_s3_with_key(
            file_path, doc.file_name,
            doc.is_private, parent_doctype,
            parent_name
        )
        
        if doc.is_private:
            method = "frappe_s3_attachment.controller.generate_file"
            file_url = """/api/method/{0}?key={1}""".format(method, key)
        else:
           method = "frappe_s3_attachment.controller.generate_file"
           file_url = """/api/method/{0}?key={1}""".format(method, key)
           
        if os.path.exists(file_path):
            os.remove(file_path)
        bucket_name = s3_upload.BUCKET
        region_name = s3_upload.S3_CLIENT.meta.region_name
        if not file_url.startswith("http"):
            updated_url = f"https://{bucket_name}.s3.{region_name}.amazonaws.com/{key}"
        
        doc = frappe.db.sql("""
                        UPDATE `tabFile` SET 
                            file_url=%s,
                            custom_updated_url=%s,
                            old_parent=%s, 
                            content_hash=%s 
                        WHERE name=%s
                    """, (
                        file_url,              
                        updated_url,           
                        'Home/Attachments',    
                        key,                   
                        doc.name               
                    ))
        frappe.db.commit()
    else:
        pass


def s3_file_regex_match(file_url):
    """
    Match the public file regex match.
    """
    return re.match(
        r'^(https:|/api/method/frappe_s3_attachment.controller.generate_file)',
        file_url
    )


@frappe.whitelist()
def migrate_existing_files():
    """
    Migrate the existing files from the public/private folder to S3.

    This function retrieves all files from the 'File' DocType, filters out files that are already
    stored in S3, and then uploads the remaining files to S3. The function also updates the file
    URLs in the 'File' DocType to point to the S3 location.

    Parameters:
    None

    Returns:
    str: A message indicating the success or failure of the migration process.
    """
    if is_s3_upload_disabled():
        return "S3 upload is disabled. Existing files were not migrated."

    validate_s3_settings(frappe.get_doc("S3 File Attachment", "S3 File Attachment"))

    # get_all_files_from_public_folder_and_upload_to_s3
    msg = "Upload Successfull"

    files_list = frappe.get_all(
        'File',
        fields=['name', 'file_url', 'file_name']
    )
    total_files = len(files_list)
    if total_files == 0:
        return msg

    successfully_uploaded = 0
    for idx, file in enumerate(files_list):
        if file['file_url'] and not s3_file_regex_match(file['file_url']):
            try:
                upload_existing_files_s3(file['name'], file['file_name'])
                successfully_uploaded += 1
            except Exception as e:
                frappe.log_error(f"{file['file_name']} Upload Failed", frappe.get_traceback())

        # Update progress
        frappe.publish_realtime(
            "progress", 
            dict(progress=(idx + 1) * 100 / total_files), 

        )

    frappe.clear_messages()
    return (
                "Partially Uploaded. "
                f"<br><br>Check <a href='/app/error-log' target='_blank'>Error Log</a> for more information."
                if successfully_uploaded != total_files
                else msg
            )

def delete_from_cloud(doc, method):
    """Delete file from s3"""

    s3 = S3Operations()
    s3.delete_from_s3(doc.content_hash)


@frappe.whitelist()
def ping():
    """
    Test function to check if api function work.
    """
    return "pong"


def get_file(key=None, file_name=None):
    """
    Fetches a signed URL for a file from S3 if the key is provided.

    Args:
        key (str): The S3 key of the file.
        file_name (str): Optional name of the file (used in URL generation).

    Returns:
        str: A signed URL for the file, or None if no key is provided.
    """
    if not key:
        return None

    s3_upload = S3Operations()
    return s3_upload.get_url(key, file_name)
