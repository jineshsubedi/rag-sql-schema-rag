-- =============================================================================
-- E-Commerce Database Schema and Mock Data Generator
-- Scale:
--   - 10 Realistic Categories
--   - 30,000 Products across categories with realistic prices, SKUs, and stock
--   - 5,000 Customers with realistic names, emails, phones, and addresses
--   - 15,000 Orders (minimum 10k) with real-world statuses and dates
--   - 45,000 Order Items with unit prices and subtotals
-- =============================================================================

-- Ensure schema exists
CREATE SCHEMA IF NOT EXISTS ecommerce;
SET search_path TO ecommerce, public;

-- Drop existing tables if needed (cascade to cleanly rebuild)
DROP TABLE IF EXISTS order_items CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS categories CASCADE;
DROP TABLE IF EXISTS customers CASCADE;

-- ---------------------------------------------------------------------------
-- 1. Categories Table
-- ---------------------------------------------------------------------------
CREATE TABLE categories (
    category_id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- 2. Products Table (30,000 products)
-- ---------------------------------------------------------------------------
CREATE TABLE products (
    product_id SERIAL PRIMARY KEY,
    category_id INT NOT NULL REFERENCES categories(category_id) ON DELETE RESTRICT,
    sku VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    price NUMERIC(10, 2) NOT NULL CHECK (price >= 0),
    stock_quantity INT NOT NULL DEFAULT 0 CHECK (stock_quantity >= 0),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- 3. Customers Table (5,000 customers)
-- ---------------------------------------------------------------------------
CREATE TABLE customers (
    customer_id SERIAL PRIMARY KEY,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    phone VARCHAR(50),
    address VARCHAR(255),
    city VARCHAR(100),
    state VARCHAR(100),
    postal_code VARCHAR(20),
    country VARCHAR(100) DEFAULT 'USA',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- 4. Orders Table (15,000 orders)
-- ---------------------------------------------------------------------------
CREATE TABLE orders (
    order_id SERIAL PRIMARY KEY,
    customer_id INT NOT NULL REFERENCES customers(customer_id) ON DELETE CASCADE,
    status VARCHAR(50) NOT NULL DEFAULT 'completed',
    total_amount NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    shipping_city VARCHAR(100),
    shipping_state VARCHAR(100),
    order_date TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- 5. Order Items Table (45,000 order items)
-- ---------------------------------------------------------------------------
CREATE TABLE order_items (
    item_id SERIAL PRIMARY KEY,
    order_id INT NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    product_id INT NOT NULL REFERENCES products(product_id) ON DELETE RESTRICT,
    quantity INT NOT NULL DEFAULT 1 CHECK (quantity > 0),
    unit_price NUMERIC(10, 2) NOT NULL CHECK (unit_price >= 0),
    subtotal NUMERIC(10, 2) NOT NULL CHECK (subtotal >= 0)
);

-- ---------------------------------------------------------------------------
-- Populate Categories
-- ---------------------------------------------------------------------------
INSERT INTO categories (name, description) VALUES
('Electronics', 'Smartphones, audio gear, smart home accessories, and gadgets'),
('Computers & Laptops', 'High-performance laptops, monitors, accessories, and components'),
('Home & Kitchen', 'Cooking equipment, modern kitchen appliances, and home decor'),
('Apparel & Fashion', 'Men and women casualwear, activewear, footwear, and accessories'),
('Books & Media', 'Bestsellers, technical guides, fiction, non-fiction, and audiobooks'),
('Sports & Outdoors', 'Camping equipment, workout tools, running gear, and sports goods'),
('Beauty & Personal Care', 'Skincare essentials, grooming, organic cosmetics, and fragrances'),
('Toys & Games', 'Family board games, brain puzzles, and collectible action figures'),
('Automotive', 'Replacement auto parts, vehicle maintenance, and car electronics'),
('Health & Wellness', 'Vitamins, herbal supplements, protein powders, and daily health care')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Populate 30,000 Products
-- ---------------------------------------------------------------------------
INSERT INTO products (category_id, sku, name, description, price, stock_quantity, is_active, created_at)
SELECT
    ((i % 10) + 1) AS category_id,
    'SKU-' || LPAD(i::text, 6, '0') AS sku,
    CASE (i % 10)
        WHEN 0 THEN 'UltraHD Smart TV Pro ' || i
        WHEN 1 THEN 'Pro Titanium Laptop ' || i
        WHEN 2 THEN 'Countertop Culinary Blender ' || i
        WHEN 3 THEN 'Relaxed-Fit Denim Jeans ' || i
        WHEN 4 THEN 'Chronicles of Discovery Vol ' || i
        WHEN 5 THEN 'Eco-Grip Fitness Yoga Mat ' || i
        WHEN 6 THEN 'Hydrating Botanical Facial Serum ' || i
        WHEN 7 THEN 'Medieval Kingdoms Strategy Game ' || i
        WHEN 8 THEN 'Heavy-Duty 12V Car Battery ' || i
        ELSE 'Daily Micronutrient Capsule ' || i
    END AS name,
    'High quality commercial grade product manufactured to stringent specifications. Model ' || i AS description,
    ROUND((12.50 + (random() * 950.0))::numeric, 2) AS price,
    FLOOR(random() * 600)::int AS stock_quantity,
    (random() > 0.03) AS is_active,
    NOW() - (random() * interval '365 days') AS created_at
FROM generate_series(1, 30000) AS s(i)
ON CONFLICT (sku) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Populate 5,000 Customers (Thousands)
-- ---------------------------------------------------------------------------
INSERT INTO customers (first_name, last_name, email, phone, address, city, state, postal_code, country, created_at)
SELECT
    (ARRAY['James', 'Mary', 'John', 'Patricia', 'Robert', 'Jennifer', 'Michael', 'Linda', 'William', 'Elizabeth', 'David', 'Barbara', 'Richard', 'Susan', 'Joseph', 'Jessica'])[1 + (i % 16)] AS first_name,
    (ARRAY['Smith', 'Johnson', 'Williams', 'Brown', 'Jones', 'Garcia', 'Miller', 'Davis', 'Rodriguez', 'Martinez', 'Hernandez', 'Lopez', 'Gonzalez', 'Wilson', 'Anderson', 'Thomas'])[1 + ((i * 7) % 16)] AS last_name,
    'customer.' || i || '.' || SUBSTR(MD5(i::text), 1, 6) || '@marketmail.com' AS email,
    '+1-555-' || LPAD((100 + (i % 900))::text, 3, '0') || '-' || LPAD((1000 + (i % 9000))::text, 4, '0') AS phone,
    (100 + (i % 900))::text || ' ' || (ARRAY['Maple Ave', 'Oak St', 'Pine Rd', 'Cedar Blvd', 'Main St', 'Elm Dr', 'Washington St', 'Lakeview Ct'])[1 + (i % 8)] AS address,
    (ARRAY['New York', 'Los Angeles', 'Chicago', 'Houston', 'Phoenix', 'Philadelphia', 'San Antonio', 'San Diego', 'Dallas', 'San Jose', 'Austin', 'Seattle', 'Denver', 'Boston', 'Miami'])[1 + (i % 15)] AS city,
    (ARRAY['NY', 'CA', 'IL', 'TX', 'AZ', 'PA', 'TX', 'CA', 'TX', 'CA', 'TX', 'WA', 'CO', 'MA', 'FL'])[1 + (i % 15)] AS state,
    LPAD((10000 + (i * 13) % 89999)::text, 5, '0') AS postal_code,
    'USA' AS country,
    NOW() - (random() * interval '730 days') AS created_at
FROM generate_series(1, 5000) AS s(i)
ON CONFLICT (email) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Populate 15,000 Orders (At least 10k)
-- ---------------------------------------------------------------------------
INSERT INTO orders (order_id, customer_id, status, total_amount, shipping_city, shipping_state, order_date)
SELECT
    i AS order_id,
    1 + (FLOOR(random() * 5000))::int AS customer_id,
    (ARRAY['completed', 'completed', 'completed', 'shipped', 'processing', 'cancelled', 'refunded'])[1 + FLOOR(random() * 7)::int] AS status,
    0.00 AS total_amount,
    (ARRAY['New York', 'Los Angeles', 'Chicago', 'Houston', 'Phoenix', 'Austin', 'Seattle', 'Denver', 'Boston', 'Miami'])[1 + (i % 10)] AS shipping_city,
    (ARRAY['NY', 'CA', 'IL', 'TX', 'AZ', 'TX', 'WA', 'CO', 'MA', 'FL'])[1 + (i % 10)] AS shipping_state,
    NOW() - (random() * interval '180 days') AS order_date
FROM generate_series(1, 15000) AS s(i)
ON CONFLICT (order_id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Populate Order Items (3 items per order average = ~45,000 records)
-- ---------------------------------------------------------------------------
INSERT INTO order_items (order_id, product_id, quantity, unit_price, subtotal)
SELECT
    o.order_id,
    p.product_id,
    item.qty,
    p.price AS unit_price,
    ROUND((item.qty * p.price)::numeric, 2) AS subtotal
FROM orders o
CROSS JOIN LATERAL (
    SELECT
        1 + ((o.order_id * 17 + k * 31) % 30000)::int AS product_id,
        1 + ((o.order_id + k) % 4)::int AS qty
    FROM generate_series(1, 2 + (o.order_id % 3)) AS k
) item
JOIN products p ON p.product_id = item.product_id
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------------
-- Calculate and update order total amounts
-- ---------------------------------------------------------------------------
UPDATE orders o
SET total_amount = sub.total
FROM (
    SELECT order_id, SUM(subtotal) AS total
    FROM order_items
    GROUP BY order_id
) sub
WHERE o.order_id = sub.order_id;

-- ---------------------------------------------------------------------------
-- Helpful Indexes for Analytical and Text-to-SQL Performance
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id);
CREATE INDEX IF NOT EXISTS idx_products_price ON products(price);
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_date ON orders(order_date);
CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_order_items_product ON order_items(product_id);
