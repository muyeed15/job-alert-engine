# User Configuration and Job Role Selection Guide

This guide explains every user preference option in the Job Alert Engine, how job listings are structured on the upstream job feed, and how to configure accurate profiles for different career tracks and seniority levels.

---

## 1. Complete User Preference Reference

Every user object in `config.json` supports the following fields. Only `name` and `email` are required; all other fields have defaults.

```json
{
  "name": "Rahim Uddin",
  "email": "rahim@example.com",
  "categories": ["IT/Telecommunication"],
  "locations": ["Dhaka"],
  "exclude_locations": ["Savar", "Ashulia", "Gazipur", "Narayanganj"],
  "include_anywhere_in_bd": true,
  "experience": {
    "min_years": 0,
    "max_years": 1,
    "include_unspecified": true
  },
  "keywords": ["Python", "Backend", "Django", "FastAPI", "Software Engineer"],
  "required_keywords": [],
  "exclude_keywords": ["Senior", "Lead", "Head of", "Manager"],
  "keyword_scope": "all",
  "job_types": ["FullTime", "Intern"],
  "workplaces": ["office", "remote", "hybrid"],
  "min_salary": 25000,
  "include_negotiable_salary": true,
  "companies": [],
  "exclude_companies": [],
  "max_age_days": null,
  "schedule": {
    "mode": "instant"
  },
  "enabled": true
}
```

### Field-by-Field Summary

| Field                            | Type     | Default     | Description                                                                                     |
| -------------------------------- | -------- | ----------- | ----------------------------------------------------------------------------------------------- |
| `name`                           | string   | required    | Recipient full name (displayed in CLI output and logs).                                         |
| `email`                          | string   | required    | Recipient email address.                                                                        |
| `categories`                     | list     | `[]` (all)  | Category names, aliases, or numeric IDs. Empty list matches all categories.                     |
| `locations`                      | list     | `[]` (all)  | Target cities, districts, or neighborhoods (for example `["Dhaka"]` or `["Uttara", "Banani"]`). |
| `exclude_locations`              | list     | `[]`        | Locations to reject even if they match `locations` (for example `["Savar", "Gazipur"]`).        |
| `include_anywhere_in_bd`         | boolean  | `true`      | When `locations` is set, also match listings marked `"Anywhere in Bangladesh"`.                 |
| `experience.min_years`           | integer  | `0`         | Minimum years of experience you want to match.                                                  |
| `experience.max_years`           | int/null | `null`      | Maximum years of experience (`null` means no upper limit).                                      |
| `experience.include_unspecified` | boolean  | `true`      | Match listings where experience is `"NA"`, `"Not Required"`, or `"Negotiable"`.                 |
| `keywords`                       | list     | `[]` (all)  | OR-matched keywords: at least one must appear when non-empty.                                   |
| `required_keywords`              | list     | `[]`        | AND-matched keywords: every keyword in this list must appear.                                   |
| `exclude_keywords`               | list     | `[]`        | Rejects a listing if any of these words appear in the **job title**.                            |
| `keyword_scope`                  | string   | `"all"`     | `"all"` searches title, company, and description snippet; `"title"` searches job title only.    |
| `job_types`                      | list     | `[]` (all)  | Allowed employment types: `"FullTime"`, `"PartTime"`, `"Contract"`, `"Intern"`, `"Freelance"`.  |
| `workplaces`                     | list     | `[]` (all)  | Allowed work modes: `"office"`, `"remote"`, `"hybrid"`.                                         |
| `min_salary`                     | int/null | `null`      | Minimum monthly salary in BDT. Rejects listings whose stated salary range is below this amount. |
| `include_negotiable_salary`      | boolean  | `true`      | When `min_salary` is set, still allow listings where salary is undisclosed (`"--"`).            |
| `companies`                      | list     | `[]` (all)  | Only match listings from these company names.                                                   |
| `exclude_companies`              | list     | `[]`        | Ignore listings from these company names.                                                       |
| `max_age_days`                   | int/null | `null`      | Only match listings published within the last `N` days.                                         |
| `schedule.mode`                  | string   | `"instant"` | `"instant"` (every 30-min poll) or `"digest"` (scheduled daily summary).                        |
| `schedule.digest_time`           | str/list | `[]`        | 24-hour time(s) for digest delivery, for example `"09:00"` or `["09:00", "18:00"]`.             |
| `enabled`                        | boolean  | `true`      | Set to `false` to pause alerts for this user while keeping their history.                       |

---

## 2. How Locations Work (Critical for Bangladesh Listings)

### Why Simple City Names Are Not Enough on Raw Feeds

In the job feed, employers frequently select a specific Thana, neighborhood, or industrial zone instead of the city name. In live data:

- Roughly 35% of Dhaka IT listings write `"Dhaka"`.
- Roughly 40% write **only the neighborhood**, such as `"Uttara Sector 12"`, `"GULSHAN 1"`, `"Banani"`, `"DOHS Mirpur"`, `"Mirpur 10"`, `"Tejgaon Industrial Area"`, `"Banglamotor"`, `"Farmgate"`, `"Motijheel"`, `"Banasree"`, `"Shyamoli"`, `"Lalmatia"`, `"Badda"`, `"Kalabagan"`, or `"Savar"`.
- Roughly 15% write `"Anywhere in Bangladesh"`.

### Smart Regional Expansion in Job Alert Engine

To make sure you never miss neighborhood-level listings, the matcher automatically expands city-level names:

1. **Setting `"locations": ["Dhaka"]`** automatically matches `"Dhaka"` **plus all Dhaka metropolitan areas and thanas**:
   - **North Dhaka**: `Uttara`, `Gulshan`, `Banani`, `Baridhara`, `Bashundhara` / `Basundhara`, `Nikunja`, `Khilkhet`, `Kuril`, `Airport`, `Cantonment`, `Badda`, `Vatara` / `Bhatara`, `Rampura`, `Banasree`, `Aftabnagar`, `Mohakhali`, `Niketan`, `Tejgaon`, `Mirpur`, `Pallabi`, `Kafrul`, `Shewrapara`, `Kazipara`, `Agargaon`, `Shyamoli`, `Kalyanpur`, `Gabtoli`, `Mohammadpur`, `Lalmatia`, `Adabor`, `Dakshinkhan`, `Uttarkhan`, `Turag`.
   - **Central & South Dhaka**: `Dhanmondi`, `Kalabagan`, `Panthapath`, `Farmgate`, `Karwan Bazar` / `Kawran Bazar`, `Banglamotor`, `Eskaton`, `Moghbazar`, `Malibagh` / `Malibag`, `Khilgaon`, `Mugda`, `Sabujbagh`, `Shahbag`, `New Market`, `Ramna`, `Kakrail`, `Segunbagicha`, `Shantinagar`, `Siddheswari`, `Paltan`, `Naya Paltan`, `Purana Paltan`, `Motijheel`, `Dilkhusha`, `Wari`, `Jatrabari`, `Lalbagh`, `Hazaribagh`, `Kotwali`, `Sutrapur`, `Shyampur`, `Demra`, `Kadamtali`, `Kamrangirchar`.
   - **Greater Dhaka Suburbs**: `Savar`, `Ashulia`, `DEPZ`, `Dhamrai`, `Keraniganj`, `Dohar`, `Nawabganj`.

2. **Excluding Outlying Suburbs / Industrial Zones (`exclude_locations`)**:
   If you live in central Dhaka and do not want to commute to factories or EPZs outside the city core, combine `locations` with `exclude_locations`:

   ```json
   "locations": ["Dhaka"],
   "exclude_locations": ["Savar", "Ashulia", "DEPZ", "Dhamrai", "Keraniganj", "Dohar", "Nawabganj", "Gazipur", "Tongi", "Narayanganj"]
   ```

3. **Targeting Specific Neighborhoods Only**:
   If you only want jobs in North Dhaka (for example near Uttara, Banani, Gulshan, Mohakhali, or Mirpur), specify those exact neighborhoods instead of `"Dhaka"`:

   ```json
   "locations": ["Uttara", "Nikunja", "Khilkhet", "Kuril", "Banani", "Gulshan", "Baridhara", "Bashundhara", "Mohakhali", "Mirpur"],
   "include_anywhere_in_bd": false
   ```

4. **Setting `"locations": ["Chattogram"]`** (or `"Chittagong"`) automatically matches:
   `Chattogram`, `Chittagong`, `Agrabad`, `Nasirabad`, `Halishahar`, `Panchlaish`, `Khulshi`, `GEC`, `Pahartali`, `Patenga`, `CEPZ`, `KEPZ`, `Chawkbazar`, `Kalurghat`, `Baizid` / `Bayazid`, `Bakalia`, `Bandar`, `Double Mooring`, `Chandgaon`, `Hathazari`, `Sitakunda`, `Mirsharai`, `Anwara`, `Patiya`, `Boalkhali`, `Raozan`, `Rangunia`, `Karnaphuli`.

5. **Setting `"locations": ["Gazipur"]`** matches `Gazipur`, `Tongi`, `Kaliakair`, `Sreepur`, `Bhaluka`.
6. **Setting `"locations": ["Narayanganj"]`** matches `Narayanganj`, `Rupganj`, `Sonargaon`, `Siddhirganj`, `Fatullah`, `Araihazar`.
7. **Other Major Districts**:
   `Sylhet`, `Rajshahi`, `Khulna`, `Barishal`, `Rangpur`, `Mymensingh`, `Cumilla` (also matches `Comilla`), `Bogura` (also matches `Bogra`), `Jashore` (also matches `Jessore`), `Kushtia`, `Cox's Bazar`, `Noakhali`, `Feni`, `Pabna`, `Sirajganj`, `Dinajpur`, `Tangail`, `Narsingdi`, `Munshiganj`, `Manikganj`, `Faridpur`, `Brahmanbaria`.

---

## 3. Complete Category Reference (All 64 Categories)

You can use the **ID**, the **Full Category Name**, or a **Short Alias** in `"categories"`.

### Functional / Professional Categories (`1` to `30`, `-10`)

| ID    | Official Category Name                 | Short Aliases Accepted                              | Typical Active Listings |
| ----- | -------------------------------------- | --------------------------------------------------- | ----------------------- |
| `1`   | `Accounting/Finance`                   | `Accounting`, `Finance`                             | ~400-450                |
| `2`   | `Bank/Non-Bank Fin. Institution`       | `Bank`, `Banking`                                   | ~90-120                 |
| `3`   | `Supply Chain/Procurement`             | `Supply Chain`, `Procurement`                       | ~150-200                |
| `4`   | `Education/Training`                   | `Education`, `Training`                             | ~300-350                |
| `5`   | `Engineer/Architect`                   | `Engineering`, `Engineer`, `Architect`              | ~450-550                |
| `6`   | `Garments/Textile`                     | `Garments`, `Textile`                               | ~550-650                |
| `7`   | `General Management/Admin`             | `Admin`, `General Management`                       | ~150-200                |
| `8`   | `IT/Telecommunication`                 | `IT`, `Software`, `Telecommunication`, `IT/Telecom` | ~250-300                |
| `9`   | `Marketing/Sales`                      | `Marketing`, `Sales`                                | ~1000-1200              |
| `10`  | `Media/Advertisement/Event Mgt.`       | `Media`, `Advertisement`                            | ~50-80                  |
| `11`  | `Healthcare/Medical`                   | `Medical`, `Healthcare`                             | ~300-380                |
| `12`  | `NGO/Development`                      | `NGO`, `Development`                                | ~240-300                |
| `13`  | `Research/Consultancy`                 | `Research`, `Consultancy`                           | ~30-60                  |
| `14`  | `Receptionist/PS`                      | `Receptionist`                                      | ~30-50                  |
| `15`  | `Data Entry/Operator/BPO`              | `BPO`, `Data Entry`                                 | ~40-70                  |
| `16`  | `Customer Service/Call Centre`         | `Customer Service`, `Call Centre`, `Call Center`    | ~180-220                |
| `17`  | `HR/Org. Development`                  | `HR`, `Human Resources`                             | ~180-220                |
| `18`  | `Design/Creative`                      | `Design`, `Creative`                                | ~130-160                |
| `19`  | `Production/Operation`                 | `Production`, `Operation`                           | ~160-200                |
| `20`  | `Hospitality/Travel/Tourism`           | `Hospitality`, `Travel`, `Tourism`                  | ~80-120                 |
| `21`  | `Beauty Care/Health & Fitness`         | `Beauty Care`, `Fitness`                            | ~15-30                  |
| `22`  | `Law/Legal`                            | `Law`, `Legal`                                      | ~40-60                  |
| `23`  | `Electrician/Construction/Repair`      | `Electrician`, `Construction`                       | ~40-70                  |
| `24`  | `Security/Support Service`             | `Security`                                          | ~40-70                  |
| `25`  | `Driving/Motor Technician`             | `Driving`                                           | ~20-40                  |
| `26`  | `Agro (Plant/Animal/Fisheries)`        | `Agro`, `Agriculture`                               | ~80-120                 |
| `27`  | `Commercial`                           | `Commercial`                                        | ~70-100                 |
| `28`  | `Company Secretary/Regulatory affairs` | `Company Secretary`, `Regulatory Affairs`           | ~15-30                  |
| `29`  | `Pharmaceutical`                       | `Pharma`, `Pharmaceuticals`                         | ~70-100                 |
| `30`  | `E-commerce/Digital Marketing`         | `Digital Marketing`, `E-commerce`, `Ecommerce`      | ~130-170                |
| `-10` | `Others (General)`                     | `Others`                                            | ~50-100                 |

### Skilled Trade / Technical Categories (`61` to `92`, `-11`)

| ID    | Category Name                        | ID   | Category Name                          |
| ----- | ------------------------------------ | ---- | -------------------------------------- |
| `61`  | `Data Entry/Computer Operator`       | `77` | `Delivery Man`                         |
| `62`  | `Mechanic/Technician`                | `78` | `Garments technician/Machine operator` |
| `63`  | `Nurse`                              | `79` | `Peon`                                 |
| `64`  | `Waiter/Waitress`                    | `80` | `Cleaner`                              |
| `65`  | `Pathologist/Lab Assistant`          | `81` | `Gardener`                             |
| `66`  | `Electrician/Electronics Technician` | `82` | `Carpenter`                            |
| `67`  | `Driver`                             | `83` | `Showroom Assistant/Salesman`          |
| `68`  | `Chef/Cook`                          | `84` | `Sales Representative (SR)`            |
| `69`  | `Housekeeper`                        | `85` | `Imam/Khatib/Muezzin`                  |
| `70`  | `Security Guard`                     | `86` | `Gym/Fitness Trainer`                  |
| `71`  | `Graphic Designer`                   | `87` | `Interpreter`                          |
| `72`  | `Welder`                             | `88` | `Beautician/Salon worker`              |
| `73`  | `Plumber/Pipe fitting`               | `89` | `Fire Safety/Firefighter`              |
| `74`  | `Sewing machine operator`            | `90` | `Boiler Operator`                      |
| `75`  | `Mason/Construction worker`          | `91` | `Caregiver/Nanny`                      |
| `76`  | `CAD Operator`                       | `92` | `Physiotherapist`                      |
| `-11` | `Other Special Skilled Jobs`         |      |                                        |

---

## 4. Choosing Experience, Salary, and Keyword Rules

### Experience Rules (`experience`)

On the upstream feed, experience appears in three main patterns:

- Range: `"1 to 3 years"`, `"0 to 2 years"`, `"2 to 5 years"`
- Lower bound: `"At least 2 years"`, `"At least 5 years"`
- Unspecified: `"NA"` (~15% to 20% of listings, especially for Internships, Management Trainee Officers, Lecturers, and Entry-Level roles)

The engine uses **interval overlap** (`[job_min, job_max]` overlaps `[user_min, user_max]`):

- **Fresh Graduate / Entry-Level (`0` to `1` year)**:
  ```json
  "experience": { "min_years": 0, "max_years": 1, "include_unspecified": true }
  ```
  Matches `"0 to 2 years"`, `"1 to 3 years"`, `"At least 1 years"`, `"At most 1 year"`, and `"NA"`, while rejecting `"2 to 4 years"` or `"At least 3 years"`.
  Combine this with `"exclude_keywords": ["Senior", "Sr.", "Lead", "Manager", "Head of", "Director", "Chief"]` so that senior roles posted with `"experience": "NA"` are rejected.
- **Junior to Mid-Level (`1` to `3` years)**:
  ```json
  "experience": { "min_years": 1, "max_years": 3, "include_unspecified": true }
  ```
- **Mid-to-Senior Level (`3` to `6` years)**:
  ```json
  "experience": { "min_years": 3, "max_years": 6, "include_unspecified": false }
  ```
  Setting `"include_unspecified": false` skips `"NA"` postings, which are usually entry-level or trainee roles.
- **Senior / Management (`5+` years)**:
  ```json
  "experience": { "min_years": 5, "max_years": null, "include_unspecified": false }
  ```

### Keyword Strategy (`keywords`, `required_keywords`, `exclude_keywords`, `keyword_scope`)

- **`keyword_scope: "all"` vs `"title"`**:
  - In the API payload, `jobTitle` has the position name (for example `"Software Engineer"`), while `eduRec` and `jobContext` hold degree requirements and brief bullet points.
  - Use `"keyword_scope": "all"` when searching for specific technologies (`"Python"`, `"Django"`, `"React"`, `"Flutter"`) that might appear in either the title or the context snippet.
  - Use `"keyword_scope": "title"` when searching for broad role names (`"Marketing"`, `"Accounts"`, `"HR"`, `"Engineer"`) so you do not match unrelated jobs that merely mention `"Marketing"` in the degree list.
- **Combining `required_keywords` (AND) with `keywords` (OR)**:
  - If you set `"required_keywords": ["Python"]` and `"keywords": ["Backend", "Django", "FastAPI", "Engineer", "Developer"]`, a job must contain `"Python"` **AND** at least one of the role words.

### Salary Filtering (`min_salary` and `include_negotiable_salary`)

- Roughly 75% to 80% of listings in the job feed have `"Salary": "--"` (Negotiable / Undisclosed), while 20% to 25% disclose a monthly range such as `"Tk. 35000 - 50000 (Monthly)"` or `"Tk. 15000 - 20000 (Monthly)"`.
- Recommended setting:
  ```json
  "min_salary": 30000,
  "include_negotiable_salary": true
  ```
  This keeps negotiable postings while automatically filtering out low-salaried listings whose maximum offered pay is below Tk. 30,000.
- If you **only** want listings that explicitly disclose a salary at or above your threshold, set `"include_negotiable_salary": false`.

---

## 5. Role Selection Matrix by Career Track

Use the table below to pick the right categories, keywords, and exclusions for each profession:

### A. Software, IT & Data Roles

| Target Role                                    | Categories                                         | Recommended `keywords`                                                                                                         | Recommended `exclude_keywords`                                                |
| ---------------------------------------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------- |
| **Python / Backend Developer**                 | `["IT/Telecommunication"]`                         | `["Python", "Django", "FastAPI", "Flask", "Backend", "Software Engineer", "Software Developer"]`                               | `["Senior", "Sr.", "Lead", "Manager", "PHP", "WordPress", ".NET", "Laravel"]` |
| **Java / .NET / Enterprise Backend**           | `["IT/Telecommunication"]`                         | `["Java", "Spring", "Spring Boot", ".NET", "C#", "ASP.NET", "Software Engineer", "Backend"]`                                   | `["Junior", "Intern"]` (for mid/senior) or `["Senior", "Lead"]` (for entry)   |
| **Node.js / Full-Stack JS Developer**          | `["IT/Telecommunication"]`                         | `["Node.js", "NodeJS", "NestJS", "Express", "Full Stack", "Full-Stack", "MERN", "TypeScript", "JavaScript"]`                   | `["WordPress", "Shopify"]`                                                    |
| **Frontend Developer (React / Next.js / Vue)** | `["IT/Telecommunication", "Design/Creative"]`      | `["Frontend", "Front-end", "React", "Next.js", "Vue", "Angular", "JavaScript", "TypeScript", "Web Developer"]`                 | `["Senior", "Lead", "Graphics"]`                                              |
| **Mobile App Developer (Flutter / Native)**    | `["IT/Telecommunication"]`                         | `["Flutter", "Android", "iOS", "Kotlin", "Swift", "React Native", "Mobile App", "Apps Developer"]`                             | `["Senior", "Lead"]`                                                          |
| **SQA / QA / Test Automation Engineer**        | `["IT/Telecommunication"]`                         | `["SQA", "QA", "Quality Assurance", "Test Engineer", "Software Tester", "Selenium", "Cypress", "Automation"]`                  | `["Garments", "Textile", "Apparel", "Factory"]`                               |
| **DevOps / Cloud / SRE / SysAdmin**            | `["IT/Telecommunication"]`                         | `["DevOps", "Cloud", "AWS", "Linux", "Docker", "Kubernetes", "System Administrator", "Server Administrator", "Data Center"]`   | `["Support Executive"]`                                                       |
| **Data Analyst / BI / AI / ML Engineer**       | `["IT/Telecommunication", "Research/Consultancy"]` | `["Data Analyst", "Data Scientist", "Machine Learning", "AI", "Business Intelligence", "Power BI", "Business Analyst", "MIS"]` | `["Data Entry"]`                                                              |
| **ERP / Odoo / SAP Consultant**                | `["IT/Telecommunication", "Accounting/Finance"]`   | `["ERP", "Odoo", "SAP", "Oracle", "Implementation"]`                                                                           | `[]`                                                                          |
| **Network / NOC / IT Support Engineer**        | `["IT/Telecommunication"]`                         | `["Network Engineer", "System Engineer", "NOC", "IT Support", "IT Executive", "IT Officer", "MikroTik", "CCNA", "Hardware"]`   | `["Software Engineer"]`                                                       |

### B. Engineering, Architecture & Manufacturing Roles

| Target Role                                       | Categories                                                                          | Recommended `keywords`                                                                                                      | Recommended `exclude_keywords`      |
| ------------------------------------------------- | ----------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | ----------------------------------- |
| **Electrical / Electronics (EEE) Engineer**       | `["Engineer/Architect", "Production/Operation", "Electrician/Construction/Repair"]` | `["Electrical", "EEE", "Sub-station", "Automation", "PLC", "Maintenance Engineer", "Power", "Electronics"]`                 | `["Civil", "Architect", "Textile"]` |
| **Mechanical / Industrial / Automobile Engineer** | `["Engineer/Architect", "Production/Operation"]`                                    | `["Mechanical", "HVAC", "Automobile", "Service Engineer", "Maintenance Engineer", "Plant Engineer", "Industrial Engineer"]` | `["Civil", "Electrical"]`           |
| **Civil / Structural / Site Engineer**            | `["Engineer/Architect", "Electrician/Construction/Repair"]`                         | `["Civil Engineer", "Site Engineer", "Project Engineer", "Structural", "Construction", "AutoCAD", "Estimator"]`             | `["Electrical", "Mechanical"]`      |
| **Architect / Interior Designer**                 | `["Engineer/Architect", "Design/Creative"]`                                         | `["Architect", "Interior", "3D Visualizer", "AutoCAD", "Landscape"]`                                                        | `["Software"]`                      |

### C. Business, Finance, Banking, Marketing & Operations Roles

| Target Role                                         | Categories                                                                                                  | Recommended `keywords`                                                                                                                                      | Recommended `exclude_keywords`         |
| --------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| **Management Trainee (MTO) / Probationary Officer** | `["Bank/Non-Bank Fin. Institution", "General Management/Admin", "Marketing/Sales", "IT/Telecommunication"]` | `["Management Trainee", "MTO", "Probationary Officer", "Trainee Officer", "Graduate Trainee", "Young Professional"]`                                        | `["Senior", "Manager"]`                |
| **Accounts, Tax & VAT / Audit**                     | `["Accounting/Finance"]`                                                                                    | `["Accounts", "Accountant", "Finance", "Tax", "VAT", "Audit", "Internal Audit", "CA", "ACCA", "Billing", "Costing"]`                                        | `["Sales", "Cashier"]`                 |
| **Banking (Credit, Risk, Branch, Cards)**           | `["Bank/Non-Bank Fin. Institution"]`                                                                        | `["Credit", "Risk", "Relationship Manager", "Branch Manager", "Cards", "Remittance", "Foreign Trade", "Compliance", "Officer"]`                             | `[]`                                   |
| **Digital Marketing, SEO & E-commerce**             | `["E-commerce/Digital Marketing", "Marketing/Sales", "Media/Advertisement/Event Mgt."]`                     | `["Digital Marketing", "SEO", "Social Media", "Meta", "Google Ads", "Content", "Brand", "E-commerce", "Growth"]`                                            | `["Field Sales", "SR", "Showroom"]`    |
| **Brand Management & Corporate Sales**              | `["Marketing/Sales"]`                                                                                       | `["Brand Manager", "Brand Executive", "Product Executive", "Corporate Sales", "Business Development", "Key Account", "ASM", "RSM"]`                         | `["Telesales", "Showroom", "Cashier"]` |
| **HR, Admin & Factory Compliance**                  | `["HR/Org. Development", "General Management/Admin"]`                                                       | `["HR", "Human Resources", "Talent Acquisition", "Recruitment", "Payroll", "Compliance", "Admin", "Welfare"]`                                               | `[]`                                   |
| **Supply Chain, Procurement & Commercial**          | `["Supply Chain/Procurement", "Commercial"]`                                                                | `["Supply Chain", "Procurement", "Purchase", "Commercial", "Import", "Export", "LC", "Logistics", "Inventory", "Warehouse", "Store"]`                       | `[]`                                   |
| **Garments Merchandising, IE & Quality**            | `["Garments/Textile"]`                                                                                      | `["Merchandiser", "Merchandising", "IE", "Industrial Engineering", "Work Study", "Quality", "GPQ", "Knitting", "Dyeing", "Washing", "Pattern"]`             | `[]`                                   |
| **NGO, Development & M&E**                          | `["NGO/Development", "Research/Consultancy"]`                                                               | `["Project Manager", "Project Officer", "Project Coordinator", "Monitoring", "Evaluation", "MEAL", "M&E", "Field Officer", "Grant", "Humanitarian"]`        | `[]`                                   |
| **Pharmaceuticals (PMD, MIO, QC/QA)**               | `["Pharmaceutical", "Healthcare/Medical"]`                                                                  | `["Product Executive", "PMD", "Medical Information Officer", "MIO", "Quality Control", "Quality Assurance", "Microbiologist", "Pharmacist", "Formulation"]` | `[]`                                   |
| **UI/UX, Graphic Design & Video Editing**           | `["Design/Creative", 71]`                                                                                   | `["UI/UX", "UI", "UX", "Product Designer", "Graphic Designer", "Visualizer", "Motion Graphics", "Video Editor", "Illustrator", "Figma"]`                    | `["Textile", "Embroidery"]`            |

---

## 6. Ready-to-Use User Profile Templates

Below are complete examples for common user types, showing both the `manage.py` command and the corresponding JSON block in `config.json`.

### Template 1: Fresh Graduate / Entry-Level Backend Developer (Central Dhaka Only)

Matches entry-level (`0-1` year or `NA`) Python/Backend/Software Engineer jobs in Dhaka (excluding outlying industrial zones like Savar/Gazipur) with instant alerts.

**CLI Command:**

```bash
.venv/bin/python manage.py add-user \
  --name "Rahim Uddin" \
  --email "rahim@example.com" \
  --categories "IT/Telecommunication" \
  --locations "Dhaka" \
  --exclude-locations "Savar,Ashulia,Gazipur,Tongi,Narayanganj" \
  --min-years 0 \
  --max-years 1 \
  --keywords "Python,Backend,Django,FastAPI,Flask,Software Engineer,Software Developer,Jr. Software Engineer" \
  --exclude-keywords "Senior,Sr.,Lead,Manager,Head of,Director,PHP,WordPress,Laravel,.NET" \
  --scope all \
  --job-types "FullTime,Intern" \
  --min-salary 25000 \
  --mode instant
```

**JSON Configuration:**

```json
{
  "name": "Rahim Uddin",
  "email": "rahim@example.com",
  "categories": ["IT/Telecommunication"],
  "locations": ["Dhaka"],
  "exclude_locations": ["Savar", "Ashulia", "Gazipur", "Tongi", "Narayanganj"],
  "include_anywhere_in_bd": true,
  "experience": {
    "min_years": 0,
    "max_years": 1,
    "include_unspecified": true
  },
  "keywords": [
    "Python",
    "Backend",
    "Django",
    "FastAPI",
    "Flask",
    "Software Engineer",
    "Software Developer",
    "Jr. Software Engineer"
  ],
  "exclude_keywords": [
    "Senior",
    "Sr.",
    "Lead",
    "Manager",
    "Head of",
    "Director",
    "PHP",
    "WordPress",
    "Laravel",
    ".NET"
  ],
  "keyword_scope": "all",
  "job_types": ["FullTime", "Intern"],
  "min_salary": 25000,
  "include_negotiable_salary": true,
  "schedule": {
    "mode": "instant"
  }
}
```

---

### Template 2: Mid-Level Full-Stack / Frontend Engineer (North Dhaka or Remote)

Targets 2 to 5 years of experience in React/Next.js/Node.js, restricted to North Dhaka tech hubs (`Uttara`, `Banani`, `Gulshan`, `Baridhara`, `Bashundhara`, `Mohakhali`, `Mirpur`) or remote work.

**CLI Command:**

```bash
.venv/bin/python manage.py add-user \
  --name "Tanvir Ahmed" \
  --email "tanvir@example.com" \
  --categories "IT/Telecommunication" \
  --locations "Uttara,Banani,Gulshan,Baridhara,Bashundhara,Basundhara,Mohakhali,Mirpur,DOHS Mirpur" \
  --min-years 2 \
  --max-years 5 \
  --exclude-unspecified \
  --keywords "React,Next.js,TypeScript,Node.js,Frontend,Front-end,Full Stack,Full-Stack" \
  --exclude-keywords "Intern,Trainee,Junior" \
  --job-types "FullTime,Contract" \
  --min-salary 50000 \
  --mode instant
```

---

### Template 3: Management Trainee (MTO) & Bank Probationary Officer Seeker

Watches all major corporate categories for MTO, Probationary Officer, and Graduate Trainee openings across Dhaka and Chattogram.

**CLI Command:**

```bash
.venv/bin/python manage.py add-user \
  --name "Farhana Islam" \
  --email "farhana@example.com" \
  --categories "Bank/Non-Bank Fin. Institution,General Management/Admin,Marketing/Sales,Supply Chain/Procurement,HR/Org. Development,Accounting/Finance" \
  --locations "Dhaka,Chattogram" \
  --min-years 0 \
  --max-years 1 \
  --keywords "Management Trainee,MTO,Probationary Officer,Graduate Trainee,Trainee Officer,Future Leader,Young Professional" \
  --exclude-keywords "Senior,Manager,AGM,DGM" \
  --scope title \
  --job-types "FullTime" \
  --mode instant
```

---

### Template 4: Digital Marketing & SEO Specialist (Twice-Daily Digest)

Watches both `E-commerce/Digital Marketing` and `Marketing/Sales` for digital marketing and SEO roles, delivered as a batched digest at `09:00` and `18:00`.

**CLI Command:**

```bash
.venv/bin/python manage.py add-user \
  --name "Karim Hasan" \
  --email "karim@example.com" \
  --categories "E-commerce/Digital Marketing,Marketing/Sales,Media/Advertisement/Event Mgt." \
  --locations "Dhaka" \
  --min-years 1 \
  --max-years 4 \
  --keywords "Digital Marketing,SEO,SEM,Social Media,Performance Marketing,Meta Ads,Google Ads,Growth,Brand Executive" \
  --exclude-keywords "Field Sales,Showroom,Sales Representative,Telesales" \
  --scope title \
  --min-salary 30000 \
  --mode digest \
  --digest-time "09:00,18:00"
```

---

### Template 5: Accounts, Finance, Tax & VAT Professional

Targets 2 to 6 years of experience in accounting, taxation, VAT, or internal audit in Dhaka or Chattogram.

**CLI Command:**

```bash
.venv/bin/python manage.py add-user \
  --name "Mahmudul Hasan" \
  --email "mahmud@example.com" \
  --categories "Accounting/Finance" \
  --locations "Dhaka,Chattogram" \
  --min-years 2 \
  --max-years 6 \
  --exclude-unspecified \
  --keywords "Accounts,Accountant,Finance,Tax,VAT,Audit,Internal Audit,Billing,Costing" \
  --exclude-keywords "Cashier,Intern,Peon" \
  --scope title \
  --min-salary 35000 \
  --mode digest \
  --digest-time "09:00"
```

---

### Template 6: University Student / Paid Internship Seeker

Matches internships and trainee positions in IT, Design, or Digital Marketing.

**CLI Command:**

```bash
.venv/bin/python manage.py add-user \
  --name "Nadia Rahman" \
  --email "nadia@example.com" \
  --categories "IT/Telecommunication,Design/Creative,E-commerce/Digital Marketing" \
  --locations "Dhaka" \
  --min-years 0 \
  --max-years 0 \
  --keywords "Intern,Internship,Trainee,Junior,Fresher" \
  --exclude-keywords "Senior,Manager,Lead" \
  --job-types "Intern,PartTime,FullTime" \
  --mode instant
```

---

### Template 7: Remote / Work-From-Home Job Seeker

Matches only listings marked as `Home` / `Remote` / `Work from Home` across IT, Customer Support, BPO, Design, and Digital Marketing.

**CLI Command:**

```bash
.venv/bin/python manage.py add-user \
  --name "Sadia Akter" \
  --email "sadia@example.com" \
  --categories "IT/Telecommunication,E-commerce/Digital Marketing,Design/Creative,Customer Service/Call Centre,Data Entry/Operator/BPO" \
  --min-years 0 \
  --max-years 3 \
  --workplaces "remote" \
  --mode instant
```

---

## 7. Testing and Tuning Your Profile

Whenever you add or edit a user profile, run `--test-fetch` to preview the exact listings that match before waiting for the next 30-minute daemon pass:

```bash
# Preview up to 25 matching listings per user (no emails sent)
.venv/bin/python main.py --test-fetch --limit 25

# If too many irrelevant jobs match, tighten keywords or add exclude_keywords:
.venv/bin/python manage.py edit-user you@example.com \
  --exclude-keywords "Senior,Sr.,Lead,Manager,Head of,PHP,WordPress" \
  --exclude-locations "Savar,Ashulia,Gazipur,Narayanganj"

# Re-test immediately
.venv/bin/python main.py --test-fetch
```
