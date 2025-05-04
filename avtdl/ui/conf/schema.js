/**
 * @param {{ [x: string]: { [x: string]: any; }; required: string[]; properties: ArrayLike<any> | { [s: string]: any; }; }} schema
 */
function flattenSchema(schema) {
    const required = schema.required || [];
    for (const [property, propertySchema] of Object.entries(schema.properties)) {
        propertySchema['required'] = Boolean(required.includes(property));
    }
    return resolveRefs(schema.properties, schema['$defs']);
}

/**
 * @param {ArrayLike<any>} schemas
 */
function flattenSchemas(schemas) {
    let resolve = ([name, schema]) => [name, flattenSchema(schema)];
    return Object.fromEntries(Object.entries(schemas).map(resolve));
}

/**
 * @param {any} schema
 * @param {{ [x: string]: any; }} defs
 */
function resolveRefs(schema, defs) {
    const resolve = (currentSchema) => {
        if (typeof currentSchema !== 'object' || currentSchema === null) {
            return currentSchema;
        }
        if (Array.isArray(currentSchema)) {
            return currentSchema.map(resolve);
        }

        let resolvedSchema = {};
        for (const key in currentSchema) {
            if (key == '$ref') {
                const refPath = currentSchema['$ref'].replace(/^#\/\$defs\//, '');
                const refContent = resolve(defs[refPath]);
                resolvedSchema = { ...resolvedSchema, ...refContent };
            } else if (key == 'allOf') {
                let allOfContent = currentSchema.allOf.map(resolve).reduce((acc, schema) => {
                    return { ...acc, ...schema };
                }, {});
                resolvedSchema = { ...resolvedSchema, ...allOfContent };
            } else if (key == 'anyOf') {
                let hasNull = false;
                let anyOfContent = {};
                currentSchema.anyOf.forEach((subtype) => {
                    const resolvedSubtype = resolve(subtype);
                    if (resolvedSubtype.type === 'null') {
                        hasNull = true;
                    } else {
                        anyOfContent = { ...resolvedSubtype };
                    }
                });
                resolvedSchema = { ...anyOfContent, ...resolvedSchema };
                // if resolvedSchema had default it should have been preserved
                // if default is yet to be processed it will overwrite the "null" set here
                if (hasNull && resolvedSchema.default === undefined) {
                    resolvedSchema.default = null;
                }
            } else {
                resolvedSchema[key] = resolve(currentSchema[key]);
            }
        }
        return resolvedSchema;
    };
    return resolve(schema);
}
