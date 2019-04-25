from __future__ import print_function
from builtins import input
from getpass import getpass
import sys
import csv
from datetime import datetime

import click
from flask.cli import with_appcontext
from flask import current_app

from .models import db, Assignment, Department, Officer, User, Salary
from .utils import get_officer


@click.command()
@with_appcontext
def make_admin_user():
    "Add confirmed administrator account"
    while True:
        username = input("Username: ")
        user = User.query.filter_by(username=username).one_or_none()
        if user:
            print("Username is already in use")
        else:
            break

    while True:
        email = input("Email: ")
        user = User.query.filter_by(email=email).one_or_none()
        if user:
            print("Email address already in use")
        else:
            break

    while True:
        password = getpass("Password: ")
        password_again = getpass("Type your password again: ")

        if password == password_again:
            break
        print("Passwords did not match")

    u = User(username=username, email=email, password=password,
             confirmed=True, is_administrator=True)
    db.session.add(u)
    db.session.commit()
    print("Administrator {} successfully added".format(username))
    current_app.logger.info('Administrator {} added with email {}'.format(username,
                                                                          email))


@click.command()
@with_appcontext
def link_images_to_department():
    """Link existing images to first department"""
    from app.models import Image, db
    images = Image.query.all()
    print("Linking images to first department:")
    for image in images:
        if not image.department_id:
            sys.stdout.write(".")
            image.department_id = 1
        else:
            print("Skipped! Department already assigned")
    db.session.commit()


@click.command()
@with_appcontext
def link_officers_to_department():
    """Links officers and units to first department"""
    from app.models import Officer, Unit, db

    officers = Officer.query.all()
    units = Unit.query.all()

    print("Linking officers and units to first department:")
    for item in officers + units:
        if not item.department_id:
            sys.stdout.write(".")
            item.department_id = 1
        else:
            print("Skipped! Object already assigned to department!")
    db.session.commit()


class ImportLog:
    updated_officers = {}
    created_officers = {}

    @classmethod
    def log_change(cls, officer, msg):
        if officer.id not in cls.created_officers:
            if officer.id not in cls.updated_officers:
                cls.updated_officers[officer.id] = []
            log = cls.updated_officers[officer.id]
        else:
            log = cls.created_officers[officer.id]
        log.append(msg)

    @classmethod
    def log_new_officer(cls, officer):
        cls.created_officers[officer.id] = []

    @classmethod
    def print_create_logs(cls):
        officers = Officer.query.filter(
            Officer.id.in_(cls.created_officers.keys())).all()
        for officer in officers:
            print('Created officer {}'.format(officer))
            for msg in cls.created_officers[officer.id]:
                print(' --->', msg)

    @classmethod
    def print_update_logs(cls):
        officers = Officer.query.filter(
            Officer.id.in_(cls.updated_officers.keys())).all()
        for officer in officers:
            print('Updates to officer {}:'.format(officer))
            for msg in cls.updated_officers[officer.id]:
                print(' --->', msg)

    @classmethod
    def print_logs(cls):
        cls.print_create_logs()
        cls.print_update_logs()

    @classmethod
    def clear_logs(cls):
        cls.updated_officers = {}
        cls.created_officers = {}


def row_has_data(row, required_fields, optional_fields):
    for field in required_fields:
        if field not in row or not row[field]:
            return False
    n_optional = 0
    for field in optional_fields:
        if field in row and row[field]:
            n_optional += 1
    if len(required_fields) > 0 or n_optional > 0:
        return True
    return False


def set_field_from_row(row, obj, attribute, allow_blank=True, fieldname=None):
    fieldname = fieldname or attribute
    if fieldname in row and (row[fieldname] or allow_blank):
        try:
            val = datetime.strptime(row[fieldname], '%Y-%m-%d').date()
        except ValueError:
            val = row[fieldname]
        setattr(obj, attribute, val)


def update_officer_from_row(row, officer):
    def update_officer_field(fieldname, allow_blank=True):
        if fieldname in row and (row[fieldname] or allow_blank) and \
                getattr(officer, fieldname) != row[fieldname]:
            ImportLog.log_change(
                officer,
                'Updated {}: {} --> {}'.format(
                    fieldname, getattr(officer, fieldname), row[fieldname]))
            setattr(officer, fieldname, row[fieldname])

    # Name and gender are the only potentially changeable fields, so update those
    update_officer_field('last_name', allow_blank=False)
    update_officer_field('first_name', allow_blank=False)
    update_officer_field('middle_initial')
    update_officer_field('suffix', allow_blank=False)
    update_officer_field('gender')

    # The rest should be static
    static_fields = [
        'unique_internal_identifier',
        'race',
        'employment_date',
        'birth_year'
    ]
    for fieldname in static_fields:
        if fieldname in row:
            if row[fieldname] == '':
                row[fieldname] = None
            if str(getattr(officer, fieldname)) != str(row[fieldname]):
                raise Exception('Officer {} {} has differing {} field. Old: {}, new: {}'.format(
                    officer.first_name,
                    officer.last_name,
                    fieldname,
                    getattr(officer, fieldname),
                    row[fieldname]
                ))

    process_assignment(row, officer, compare=True)
    process_salary(row, officer, compare=True)


def create_officer_from_row(row, department_id):
    officer = Officer()
    officer.department_id = department_id

    set_field_from_row(row, officer, 'last_name', allow_blank=False)
    set_field_from_row(row, officer, 'first_name', allow_blank=False)
    set_field_from_row(row, officer, 'middle_initial')
    set_field_from_row(row, officer, 'suffix')
    set_field_from_row(row, officer, 'race')
    set_field_from_row(row, officer, 'gender')
    set_field_from_row(row, officer, 'employment_date', allow_blank=False)
    set_field_from_row(row, officer, 'birth_year')
    set_field_from_row(row, officer, 'unique_internal_identifier')
    db.session.add(officer)
    db.session.flush()

    ImportLog.log_new_officer(officer)

    process_assignment(row, officer, compare=False)
    process_salary(row, officer, compare=False)


def process_assignment(row, officer, compare=False):
    assignment_fields = {
        'required': [],
        'optional': [
            'star_no',
            'rank',
            'unit',
            'star_date',
            'resign_date']
    }

    # See if the row has assignment data
    if row_has_data(row, assignment_fields['required'], assignment_fields['optional']):
        add_assignment = True
        if compare:
            # Get existing assignments for officer and compare to row data
            assignments = Assignment.query.filter_by(officer_id=officer.id).all()
            all_assignment_fields = assignment_fields['required'] + assignment_fields['optional']
            for assignment in assignments:
                i = 0
                for fieldname in all_assignment_fields:
                    current = getattr(assignment, fieldname)
                    # Test if fields match between row and existing assignment
                    if (current and fieldname in row and row[fieldname] == current) or \
                            (not current and (fieldname not in row or not row[fieldname])):
                        i += 1
                if i == len(all_assignment_fields):
                    # Found match, so don't add new assignment
                    add_assignment = False

        if add_assignment:
            # create new assignment
            assignment = Assignment()
            assignment.officer_id = officer.id
            set_field_from_row(row, assignment, 'star_no')
            set_field_from_row(row, assignment, 'rank')
            set_field_from_row(row, assignment, 'unit')
            set_field_from_row(row, assignment, 'star_date', allow_blank=False)
            set_field_from_row(row, assignment, 'resign_date', allow_blank=False)
            db.session.add(assignment)
            db.session.flush()

            ImportLog.log_change(officer, 'Added assignment: {}'.format(assignment))


def process_salary(row, officer, compare=False):
    salary_fields = {
        'required': [
            'salary',
            'salary_year',
            'salary_is_fiscal_year'],
        'optional': ['overtime_pay']
    }

    # See if the row has salary data
    if row_has_data(row, salary_fields['required'], salary_fields['optional']):
        is_fiscal_year = False
        if row['salary_is_fiscal_year'] == 'y' or row['salary_is_fiscal_year'] == 'Y' \
                or row['salary_is_fiscal_year'] == 'True' or row['salary_is_fiscal_year'] == 'true':
            is_fiscal_year = True

        add_salary = True
        if compare:
            # Get existing salaries for officer and compare to row data
            salaries = Salary.query.filter_by(officer_id=officer.id).all()
            for salary in salaries:
                from decimal import Decimal
                print(vars(salary))
                print(row)
                if Decimal('%.2f' % salary.salary) == Decimal('%.2f' % float(row['salary'])) and \
                        salary.year == int(row['salary_year']) and \
                        salary.is_fiscal_year == is_fiscal_year and \
                        ((salary.overtime_pay and 'overtime_pay' in row and
                            Decimal('%.2f' % salary.overtime_pay) == Decimal('%.2f' % float(row['overtime_pay']))) or
                            (not salary.overtime_pay and ('overtime_pay' not in row or not row['overtime_pay']))):
                    # Found match, so don't add new salary
                    add_salary = False

        if add_salary:
            # create new salary
            salary = Salary(
                officer_id=officer.id,
                salary=float(row['salary']),
                year=int(row['salary_year']),
                is_fiscal_year=is_fiscal_year,
            )
            if 'overtime_pay' in row and row['overtime_pay']:
                salary.overtime_pay = float(row['overtime_pay'])
            db.session.add(salary)
            db.session.flush()

            ImportLog.log_change(officer, 'Added salary: {}'.format(salary))


@click.command()
@click.argument('filename')
@with_appcontext
def bulk_add_officers(filename):
    """Bulk adds officers."""
    with open(filename, 'r') as f:
        ImportLog.clear_logs()
        csvfile = csv.DictReader(f)
        departments = {}

        required_fields = [
            'department_id',
            'first_name',
            'last_name',
        ]

        # Assert required fields are in CSV file
        for field in required_fields:
            if field not in csvfile.fieldnames:
                raise Exception('Missing required field {}'.format(field))
        if 'star_no' not in csvfile.fieldnames and 'unique_internal_identifier' not in csvfile.fieldnames:
            raise Exception('CSV file must include either badge numbers or unique identifiers for officers')

        for row in csvfile:
            department_id = row['department_id']
            department = departments.get(department_id)
            if row['department_id'] not in departments:
                department = Department.query.filter_by(id=department_id).one_or_none()
                if department:
                    departments[department_id] = department
                else:
                    raise Exception('Department ID {} not found'.format(department_id))

            # check for existing officer based on unique ID or name/badge
            if 'unique_internal_identifier' in csvfile.fieldnames and row['unique_internal_identifier']:
                officer = Officer.query.filter_by(
                    department_id=department_id,
                    unique_internal_identifier=row['unique_internal_identifier']
                ).one_or_none()
            elif 'star_no' in csvfile.fieldnames and row['star_no']:
                officer = get_officer(department_id, row['star_no'],
                                      row['first_name'], row['last_name'])
            else:
                raise Exception('Officer {} {} missing badge number and unique identifier'.format(row['first_name'],
                                                                                                  row['last_name']))

            if officer:
                update_officer_from_row(row, officer)
            else:
                create_officer_from_row(row, department_id)

        db.session.commit()

        ImportLog.print_logs()

        return len(ImportLog.created_officers), len(ImportLog.updated_officers)
